"""``browser_exec`` 的执行底座：**子进程**跑那段 Python ＋ 本地桥调回主进程。

上游 Hermes 的 ``tools/browser_use_cli.py`` 靠他们 venv 里的 ``browser_harness`` 包；
**我们没有那个依赖** ⇒ 这一层自己写（``WP-BROWSER-EXEC`` 16a），形状照上游：

* **子进程**：那段 Python 是模型写的 ⇒ 它在 agent 进程里跑，就等于把 agent 的权限给出去
  （import 任意模块、读文件、起进程）。子进程 ＋ 最小环境 ＋ 超时**杀整个进程组** ⇒ 边界清楚。
* **桥**：子进程里的 helper 通过一个**只监听 127.0.0.1、带一次性 token** 的 HTTP 端点
  调回主进程；主进程那边是常驻 CDP 会话那层（:mod:`tools.browser_supervisor`）。
* **workspace**：每 task 一个私有目录，跨调用可读（上游同款：Python 变量不持久，文件持久）。
* **redact**：回给模型的输出过一遍 :func:`agent.redact.redact_sensitive_text`。

**这一版（16a）的边界**：helper 作用在**当前页**（supervisor 跟的就是第一个页面会话）⇒
多标签接管（``new_tab`` 切页）属 16c。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 300
MIN_TIMEOUT_S = 5
MAX_TIMEOUT_S = 1800
STDOUT_CAP_CHARS = 50_000
STDERR_CAP_CHARS = 10_000
BRIDGE_CALL_TIMEOUT_S = 60.0

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNNER = _REPO_ROOT / "tools" / "browser_exec_runner.py"


def clamp_timeout(timeout_s: Any) -> int:
    try:
        return max(MIN_TIMEOUT_S, min(int(timeout_s), MAX_TIMEOUT_S))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


# ── 桥（主进程侧） ──────────────────────────────────────────────────────────


class _BridgeHandler(BaseHTTPRequestHandler):
    """``POST /cdp`` ＋ ``POST /eval``；token 鉴权；失败也是一句人话。"""

    backend: Callable[[str, Dict[str, Any]], Dict[str, Any]] = staticmethod(
        lambda path, payload: {"ok": False, "error": "no backend"}
    )
    token: str = ""

    def log_message(self, *_args: Any) -> None:  # noqa: D401 - 静默
        """别把每次 helper 调用都打进日志。"""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
        if self.headers.get("X-Plobi-Bridge-Token", "") != self.token:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001 - 坏载荷也算一次失败
            payload = {}
        path = self.path.split("?")[0]
        try:
            result = self.backend(path, payload if isinstance(payload, dict) else {})
        except Exception as exc:  # noqa: BLE001 - 桥不许把异常泄给子进程
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        body = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


class BrowserBridge:
    """一个只监听 127.0.0.1、带一次性 token 的本地端点。"""

    def __init__(self, backend: Callable[[str, Dict[str, Any]], Dict[str, Any]]):
        self.token = secrets.token_urlsafe(24)
        handler = type(
            "_BoundBridgeHandler",
            (_BridgeHandler,),
            {"backend": staticmethod(backend), "token": self.token},
        )
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="plobi-browser-bridge", daemon=True
        )
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[0], self._server.server_address[1]
        return f"http://{host}:{port}"

    def close(self) -> None:
        for closer in (self._server.shutdown, self._server.server_close):
            try:
                closer()
            except Exception:  # noqa: BLE001 - 收桥失败也不出声
                pass


# ── workspace ────────────────────────────────────────────────────────────────


def workspace_dir(task_id: Optional[str]) -> Path:
    """每 task 一个私有目录（跨调用可读；Python 变量不持久，文件持久）。"""
    safe = "".join(ch for ch in str(task_id or "default") if ch.isalnum() or ch in "-_.")[:60] or "default"
    base = Path(tempfile.gettempdir()) / "plobi-browser-exec" / "workspace" / safe
    base.mkdir(parents=True, exist_ok=True)
    return base


# ── 子进程 ────────────────────────────────────────────────────────────────────


def _child_env(bridge: BrowserBridge, workspace: Path) -> Dict[str, str]:
    """子进程环境：沿用沙箱那套清洗 ＋ 桥的三个参数 ＋ 仓库根（要 import 到我们的模块）。"""
    try:
        from tools.code_execution_tool import _scrub_child_env

        env = _scrub_child_env(dict(os.environ))
    except Exception:  # noqa: BLE001 - 拿不到清洗器就用最小环境
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TEMP", "TMP", "LANG")}
    env["PLOBI_BROWSER_BRIDGE_URL"] = bridge.url
    env["PLOBI_BROWSER_BRIDGE_TOKEN"] = bridge.token
    env["PLOBI_BROWSER_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO_ROOT), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kill_child_tree(proc: subprocess.Popen) -> None:
    """超时之后**连人带子树**一起收掉（Windows：taskkill /T；POSIX：进程组）。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        else:
            try:
                import signal

                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
    except Exception as exc:  # noqa: BLE001 - 收不干净也别把调用方带崩
        logger.debug("browser_exec: killing the child tree failed: %s", exc)
        try:
            proc.kill()
        except Exception:
            pass


def run_browser_exec(
    code: str,
    *,
    backend: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    task_id: Optional[str] = None,
    session: str = "",
    timeout_s: Any = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    """跑那段 Python，返回上游同款形状的结果。

    ``backend`` 是桥背后那层（主进程里的 CDP 调用）。
    """
    from agent.redact import redact_sensitive_text

    workspace = workspace_dir(task_id)
    script = workspace / "browser_exec_task.py"
    script.write_text(code or "", encoding="utf-8")

    bridge = BrowserBridge(backend)
    env = _child_env(bridge, workspace)
    timeout = clamp_timeout(timeout_s)

    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
        "cwd": str(workspace),
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True

    cmd = [sys.executable, str(_RUNNER), str(script)]
    try:
        proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - 固定参数列表，没有 shell
    except OSError as exc:
        bridge.close()
        return {"success": False, "error": f"Failed to start the browser_exec runner: {exc}"}

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_child_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001 - 管道已经断了
            stdout, stderr = "", ""
        bridge.close()
        return {
            "success": False,
            "error": (
                f"browser_exec timed out after {timeout}s. The browser may still be working; "
                "retry with a larger timeout_s, or split the work into several calls that append "
                "to files in the workspace — anything already written there is preserved."
            ),
            "workspace": str(workspace),
            "session": session,
        }
    finally:
        bridge.close()

    out = redact_sensitive_text(stdout or "", force=True)
    err = redact_sensitive_text((stderr or "").strip(), force=True)
    if len(out) > STDOUT_CAP_CHARS:
        out = out[:STDOUT_CAP_CHARS] + "\n… (stdout truncated)"
    if len(err) > STDERR_CAP_CHARS:
        err = err[:STDERR_CAP_CHARS] + "\n… (stderr truncated)"

    result: Dict[str, Any] = {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": out,
        "workspace": str(workspace),
    }
    if session:
        result["session"] = session
    if err:
        result["stderr"] = err
    return result


def cleanup_workspace(task_id: Optional[str]) -> None:
    """把这个 task 的 workspace 收掉（清理命令用）。"""
    safe = "".join(ch for ch in str(task_id or "default") if ch.isalnum() or ch in "-_.")[:60] or "default"
    shutil.rmtree(
        Path(tempfile.gettempdir()) / "plobi-browser-exec" / "workspace" / safe, ignore_errors=True
    )
