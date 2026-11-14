"""本地浏览器启动器：起一个 Chromium 家族浏览器，拿到它的 CDP 端点。

上游 Hermes 有同名的一层（``tools/browser_launcher.py``），干的是同一件事；**我们没有他们
那个 ``browser_harness`` 包** ⇒ 这一层得自己写（``WP-BROWSER-EXEC`` 16a）。

规矩（照上游那份的形状）：

* 找本机 Chromium 家族（Chrome／Edge／Chromium）；
* 用 ``--remote-debugging-port=0`` 起（**0 ＝ 让它自己挑一个空闲端口**，免得撞上别的进程）；
* **私有用户目录**（0700）⇒ 不碰用户自己的浏览器资料，也不留共享 cookie；
* 端点从 ``<user-data-dir>/DevToolsActivePort`` 读（第一行端口、第二行 ``/devtools/browser/…``）；
* **回收器**：进程没了就把私有目录收掉（不留垃圾，也不留孤儿浏览器）。

这一层只管「起浏览器 ＋ 拿到端点」；**驱动**那一层在 :mod:`tools.browser_exec_runtime`。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 启动一个 headless Chromium 家族浏览器大约要 1-3 秒（Windows 上更慢些）。
_START_TIMEOUT_S = 30.0
# DevToolsActivePort 的读法：等它出现，最多等这么久。
_PORT_FILE_TIMEOUT_S = 30.0

_HEADLESS_FLAGS = [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-extensions",
    "--disable-features=Translate,OptimizationHints",
    "--no-sandbox",
]


def _registry_candidates() -> List[Path]:
    """从 Windows 注册表的 App Paths 找浏览器（**不依赖环境变量**）。

    为什么不能只看环境变量：这仓的测试跑在 ``env -i`` 的干净环境里
    （``scripts/run_tests.sh``），那里**没有** ``PROGRAMFILES`` ⇒ 只看环境变量的
    查找在那儿会「找不到浏览器」⇒ 真浏览器那几条测试被静默跳过。注册表才是
    权威：装在哪就在哪。
    """
    out: List[Path] = []
    try:
        import winreg
    except ImportError:
        return out

    for hive, sub_key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ):
        for exe in ("chrome.exe", "msedge.exe", "chromium.exe", "brave.exe"):
            try:
                with winreg.OpenKey(hive, f"{sub_key}\\{exe}") as key:
                    value, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            if value:
                out.append(Path(str(value)))
    return out


def _candidate_binaries() -> List[Path]:
    """本机可能的 Chromium 家族可执行文件（按偏好排序）。"""
    names = ("chrome.exe", "msedge.exe", "chromium.exe", "brave.exe")
    roots: List[Path] = []

    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        raw = os.environ.get(var)
        if raw:
            roots.append(Path(raw))

    # 硬编码的默认安装根（**不依赖环境变量**——测试跑在 env -i 的干净环境里）。
    roots.extend(
        [
            Path(r"C:\Program Files"),
            Path(r"C:\Program Files (x86)"),
        ]
    )

    candidates: List[Path] = []
    for root in roots:
        for name in names:
            candidates.extend(
                [
                    root / "Google" / "Chrome" / "Application" / name,
                    root / "Microsoft" / "Edge" / "Application" / name,
                    root / "Chromium" / "Application" / name,
                    root / "BraveSoftware" / "Brave-Browser" / "Application" / name,
                ]
            )

    # 注册表 App Paths（Windows 上最权威的一处）
    candidates.extend(_registry_candidates())

    # PATH 上的同名可执行文件（Linux/macOS 上是 google-chrome / chromium 等）
    for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                "microsoft-edge", "brave-browser"):
        found = shutil.which(exe)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    out: List[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                out.append(path)
        except OSError:
            continue
    return out


def find_chromium() -> Optional[Path]:
    """本机第一个可用的 Chromium 家族浏览器，或 None。"""
    for path in _candidate_binaries():
        logger.debug("browser_launcher: candidate %s", path)
        return path
    return None


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """连人带子树一起收掉（Windows 用 taskkill /T；POSIX 用进程组）。"""
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
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
    except Exception as exc:  # noqa: BLE001 - 收不干净也不能把调用方带崩
        logger.debug("browser_launcher: killing the browser tree failed: %s", exc)
        try:
            proc.kill()
        except Exception:
            pass


class BrowserInstance:
    """一个本地浏览器实例（进程 ＋ 私有用户目录 ＋ CDP 端点）。"""

    def __init__(self, binary: Path, user_data_dir: Path, proc: subprocess.Popen, cdp_url: str):
        self.binary = binary
        self.user_data_dir = user_data_dir
        self.proc = proc
        self.cdp_url = cdp_url

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        """收掉进程并删掉私有目录（连它的父目录一起——留个空壳也是垃圾）。"""
        _kill_process_tree(self.proc)
        # 给它一点时间真的退（Windows 上 taskkill 是异步的）⇒ 不然目录删不掉。
        for _ in range(20):
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        shutil.rmtree(self.user_data_dir, ignore_errors=True)
        shutil.rmtree(self.user_data_dir.parent, ignore_errors=True)


def _read_devtools_endpoint(user_data_dir: Path, timeout: float) -> Optional[str]:
    """等 ``DevToolsActivePort`` 出现，读出 ``ws://127.0.0.1:<port><path>``。"""
    port_file = user_data_dir / "DevToolsActivePort"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if port_file.is_file():
                raw = port_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if len(raw) >= 2 and raw[0].strip().isdigit():
                    return f"ws://127.0.0.1:{raw[0].strip()}{raw[1].strip()}"
        except OSError:
            pass
        time.sleep(0.15)
    return None


def launch_browser(cache_key: str = "default") -> BrowserInstance:
    """起一个本地浏览器，返回它的 CDP 端点。

    抛 :class:`RuntimeError`（带一句人话）当本机没有可用的浏览器。
    """
    binary = find_chromium()
    if binary is None:
        raise RuntimeError(
            "No Chromium-family browser found on this machine (Chrome / Edge / Chromium). "
            "Install one, or point the browser at a CDP endpoint "
            "(BROWSER_CDP_URL / browser.cdp_url in config.yaml)."
        )

    safe = "".join(ch for ch in str(cache_key) if ch.isalnum() or ch in "-_.")[:60] or "default"
    base = Path(tempfile.gettempdir()) / "plobi-browser-exec" / safe
    base.mkdir(parents=True, exist_ok=True)
    user_data_dir = base / "profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(user_data_dir, 0o700)
    except OSError:
        pass

    cmd = [
        str(binary),
        f"--remote-debugging-port=0",
        f"--user-data-dir={user_data_dir}",
        *(_HEADLESS_FLAGS if os.name != "nt" or True else []),
        "about:blank",
    ]
    kwargs: Dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True

    logger.info("browser_launcher: starting %s (profile %s)", binary.name, user_data_dir)
    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - 固定参数列表，没有 shell

    endpoint = _read_devtools_endpoint(user_data_dir, _PORT_FILE_TIMEOUT_S)
    if not endpoint:
        _kill_process_tree(proc)
        shutil.rmtree(base, ignore_errors=True)
        raise RuntimeError(
            "The local browser started but never published a DevTools endpoint "
            f"(waited {_PORT_FILE_TIMEOUT_S:.0f}s). Check that {binary.name} can run headless here."
        )

    return BrowserInstance(binary, user_data_dir, proc, endpoint)


class LauncherRegistry:
    """按 cache key 记着起过的浏览器（一个 key 一个浏览器），并负责收尾。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instances: Dict[str, BrowserInstance] = {}

    def get_or_launch(self, cache_key: str = "default") -> BrowserInstance:
        with self._lock:
            instance = self._instances.get(cache_key)
            if instance is not None and instance.alive:
                return instance
            if instance is not None:
                instance.stop()
            instance = launch_browser(cache_key)
            self._instances[cache_key] = instance
            return instance

    def stop(self, cache_key: str) -> None:
        with self._lock:
            instance = self._instances.pop(cache_key, None)
        if instance is not None:
            instance.stop()

    def stop_all(self) -> None:
        with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
        for instance in instances:
            instance.stop()


LAUNCHER_REGISTRY = LauncherRegistry()


@atexit.register
def _stop_all_launched() -> None:
    try:
        LAUNCHER_REGISTRY.stop_all()
    except Exception:  # noqa: BLE001 - 退出时收不干净也别出声
        pass
