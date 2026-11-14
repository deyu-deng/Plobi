"""``browser_exec``：写一段 Python，驱动一个真浏览器。

上游 Hermes 的同名工具（``tools/browser_use_cli.py``）是 Hermes **唯一**的浏览器核心工具：
一把 ``browser_exec`` 顶掉了原先那十几把点击类工具。**我们这一刀**（``WP-BROWSER-EXEC``）
照那个形状来——但**底座是我们自己的**（我们没有他们那个 ``browser_harness`` 包）：

* **端点**：``BROWSER_CDP_URL``（``/browser connect`` 的活覆盖）→ ``browser.cdp_url``
  配置 → **自己起一个本地浏览器**（:mod:`tools.browser_launcher`）。
* **会话**：常驻 CDP 会话那层（:mod:`tools.browser_supervisor`）——每 task 一条连接。
* **执行**：子进程跑那段 Python（:mod:`tools.browser_exec_runtime`），helper 通过本地桥
  调回主进程。

**这一版（16a）的边界**：helper 作用在**当前页**；多标签接管、具名会话各自的浏览器、
端点优先级链的完整化属 16c。旧那十几把点击类工具在 16b 之前**照旧注册**（这一刀先把
底座立起来）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BROWSER_EXEC_TOOL = "browser_exec"

_HEADER = (
    "Drive a real browser with Python. `code` runs as full Python (stdlib available); "
    "print(...) any data you need back. Pre-imported helpers: `goto_url(url)`, "
    "`wait_for_load()`, `page_info()` (title / url / visible text), `js(expr)` (evaluate a "
    "JS expression and return its value), `fill_input(selector, text)`, "
    "`capture_screenshot()` (saves a PNG in the workspace and returns its path), and "
    "`cdp(method, **params)` (raw CDP on the current page).\n\n"
    "STATE: the browser session and the workspace persist across calls; Python variables do "
    "NOT (a fresh interpreter every call). The workspace path is in every result's "
    "`workspace` field; for multi-item tasks, append each batch to a JSON/CSV file there, "
    "then read it back and aggregate in code — verify the collected count against what was "
    "asked before answering."
)

BROWSER_EXEC_SCHEMA = {
    "name": BROWSER_EXEC_TOOL,
    "description": _HEADER,
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python using the pre-imported browser helpers.",
            },
            "session": {
                "type": "string",
                "description": (
                    "Named browser session — its own browser, so concurrent tasks do not "
                    "share tabs. Reuse the same name on every related call."
                ),
            },
            "timeout_s": {
                "type": "integer",
                "default": 300,
                "description": "Max seconds to wait for the code to finish (max 1800).",
            },
        },
        "required": ["code"],
    },
}


def _resolve_endpoint(session: str) -> str:
    """这一会话用哪个浏览器：先看已接的端点，没有就自己起一个。"""
    try:
        from tools.browser_cdp_tool import _resolve_cdp_endpoint

        endpoint = (_resolve_cdp_endpoint() or "").strip()
    except Exception as exc:  # noqa: BLE001 - 解析失败就自己起一个
        logger.debug("browser_exec: CDP endpoint lookup failed: %s", exc)
        endpoint = ""
    if endpoint:
        return endpoint

    from tools.browser_launcher import LAUNCHER_REGISTRY

    instance = LAUNCHER_REGISTRY.get_or_launch(session or "default")
    return instance.cdp_url


def _supervisor_for(task_id: str, endpoint: str):
    """这一会话的常驻 CDP 会话（按 task 一条）。"""
    from tools.browser_supervisor import SUPERVISOR_REGISTRY

    return SUPERVISOR_REGISTRY.get_or_start(task_id=task_id, cdp_url=endpoint)


def _make_backend(supervisor):
    """桥背后那层：``/cdp`` 走裸 CDP，``/eval`` 走页面里的 JS。"""

    def backend(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if path == "/cdp":
            method = str(payload.get("method") or "")
            if not method:
                return {"ok": False, "error": "no CDP method given"}
            params = payload.get("params")
            return supervisor.send_cdp(method, params if isinstance(params, dict) else None)
        if path == "/eval":
            expression = str(payload.get("expression") or "")
            if not expression:
                return {"ok": False, "error": "no expression given"}
            outcome = supervisor.evaluate_runtime(expression)
            if not outcome.get("ok"):
                return {"ok": False, "error": outcome.get("error") or "evaluation failed"}
            return {"ok": True, "result": outcome.get("result")}
        return {"ok": False, "error": f"unknown bridge path {path!r}"}

    return backend


def browser_exec(
    code: str,
    session: str = "",
    timeout_s: int = 300,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """跑那段 Python，返回结果字典（不是 JSON 串——注册表那层会序列化）。"""
    if not (code or "").strip():
        return {
            "success": False,
            "error": (
                "No code provided. Pass Python that uses the pre-imported helpers, e.g. "
                "goto_url('https://example.com') then print(page_info())."
            ),
        }

    name = (session or "").strip()
    if name and not all(ch.isalnum() or ch in "-_" for ch in name):
        return {
            "success": False,
            "error": (
                f"Invalid session name {session!r}: use letters, digits, dashes, or "
                "underscores (e.g. 'r7k2')."
            ),
        }

    from tools.browser_exec_runtime import run_browser_exec

    key = name or (task_id or "default")
    try:
        endpoint = _resolve_endpoint(key)
        supervisor = _supervisor_for(task_id or "default", endpoint)
    except Exception as exc:  # noqa: BLE001 - 起不来要说人话
        logger.warning("browser_exec: no usable browser: %s", exc)
        return {"success": False, "error": str(exc)}

    try:
        return run_browser_exec(
            code,
            backend=_make_backend(supervisor),
            task_id=task_id,
            session=name,
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - 失败要变成一句人话
        logger.warning("browser_exec: failed: %s", exc)
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _handle_browser_exec(args: Dict[str, Any], **kw: Any) -> str:
    from tools.registry import tool_result

    outcome = browser_exec(
        code=str(args.get("code") or ""),
        session=str(args.get("session") or ""),
        timeout_s=args.get("timeout_s", 300),
        task_id=kw.get("task_id"),
    )
    return tool_result(outcome)


def browser_exec_available() -> bool:
    """check_fn：有浏览器可起 ＝ 这把工具在。

    **只读**：不启动浏览器、不开端口（上游同款纪律：check_fn 只答「可达／可选」，
    「这次会话是不是桌面」那种表面判断不住在这里）。
    """
    try:
        from tools.browser_launcher import find_chromium

        if find_chromium() is not None:
            return True
    except Exception:  # noqa: BLE001 - 查不到就退到「已接的端点」
        pass
    try:
        from tools.browser_cdp_tool import _resolve_cdp_endpoint

        return bool((_resolve_cdp_endpoint() or "").strip())
    except Exception:  # noqa: BLE001
        return False


from tools.registry import registry

registry.register(
    name=BROWSER_EXEC_TOOL,
    toolset="browser",
    schema=BROWSER_EXEC_SCHEMA,
    handler=_handle_browser_exec,
    check_fn=browser_exec_available,
    emoji="🌐",
)
