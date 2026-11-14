"""``browser_exec`` 那一侧：**子进程**里预置的浏览器 helper。

这段模块跑在**子进程**里（模型写的那段 Python 在同一个进程里 exec）。所有 helper
都通过**本地桥**调回主进程（主进程那边是常驻 CDP 会话那层
:mod:`tools.browser_supervisor`）——子进程自己不碰浏览器。

helper 命名照上游（``tools/browser_use_cli.py`` 的那套）：

* ``cdp(method, **params)`` —— 裸 CDP 调用；
* ``js(expression)`` —— 在页面里跑一句 JS，回它的值；
* ``goto_url(url)`` —— 导航当前页；
* ``wait_for_load()`` —— 等页面 load 完；
* ``page_info()`` —— 当前页的标题／地址／可见文本摘要；
* ``fill_input(selector, text)`` —— 往输入框里填字；
* ``capture_screenshot(path=None)`` —— 截图存到 workspace，回路径。

**这一版（16a）的边界**：helper 作用在**当前页**（supervisor 跟的就是第一个页面会话）
⇒ 多标签接管（``new_tab`` 切页）属 16c。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

_BRIDGE_URL = os.environ.get("PLOBI_BROWSER_BRIDGE_URL", "").rstrip("/")
_TOKEN = os.environ.get("PLOBI_BROWSER_BRIDGE_TOKEN", "")
WORKSPACE = os.environ.get("PLOBI_BROWSER_WORKSPACE", "")


def _bridge(path: str, payload: dict) -> dict:
    """调主进程那一侧；失败就把主进程给的那句话原样带出来。"""
    if not _BRIDGE_URL:
        raise RuntimeError("browser_exec: no bridge endpoint (PLOBI_BROWSER_BRIDGE_URL is unset)")
    request = urllib.request.Request(
        _BRIDGE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Plobi-Bridge-Token": _TOKEN,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"browser_exec: the browser bridge is unreachable ({exc})") from exc
    try:
        out = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - 坏响应也算失败
        raise RuntimeError(f"browser_exec: the browser bridge returned garbage ({exc})") from exc
    if not isinstance(out, dict):
        raise RuntimeError("browser_exec: the browser bridge returned an unexpected shape")
    if not out.get("ok"):
        raise RuntimeError(str(out.get("error") or "browser_exec: the browser call failed"))
    return out.get("result")


def cdp(method: str, **params):
    """裸 CDP 调用（当前页）。例如 ``cdp("Page.reload")``。"""
    return _bridge("/cdp", {"method": method, "params": params})


def js(expression: str):
    """在页面里跑一句 JS，回它的值。"""
    return _bridge("/eval", {"expression": expression})


def goto_url(url: str) -> str:
    """导航当前页到 ``url``，等 load 完，回最终地址。"""
    js(
        "window.location.href = %s; true" % json.dumps(str(url))
    )
    wait_for_load()
    return str(js("window.location.href") or url)


def wait_for_load(timeout_s: float = 20.0) -> bool:
    """等页面 load 完（``document.readyState`` 变成 complete）。"""
    import time

    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        try:
            state = js("document.readyState")
        except Exception:
            time.sleep(0.2)
            continue
        if str(state) == "complete":
            return True
        time.sleep(0.2)
    return False


def page_info() -> dict:
    """当前页的标题／地址／可见文本摘要。"""
    return js(
        "({title: document.title || '', url: window.location.href || '',"
        " text: (document.body ? document.body.innerText : '') || ''})"
    ) or {}


def fill_input(selector: str, text: str) -> bool:
    """往 ``selector`` 指的输入框里填字（并派发 input/change 事件，页面的框架才认）。"""
    return bool(
        js(
            "(() => { const el = document.querySelector(%s); if (!el) return false;"
            " el.focus(); el.value = %s;"
            " el.dispatchEvent(new Event('input', {bubbles: true}));"
            " el.dispatchEvent(new Event('change', {bubbles: true})); return true; })()"
            % (json.dumps(str(selector)), json.dumps(str(text)))
        )
    )


def capture_screenshot(path: str = "") -> str:
    """截图，存到 workspace（没给路径就自己起一个），回那个路径。"""
    import base64
    import time

    data = cdp("Page.captureScreenshot", format="png")
    raw = data.get("data") if isinstance(data, dict) else None
    if not raw:
        raise RuntimeError("browser_exec: the browser returned no screenshot data")
    target = path or os.path.join(WORKSPACE or ".", f"screenshot-{int(time.time() * 1000)}.png")
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(base64.b64decode(raw))
    return target


_HELPERS = {
    "cdp": cdp,
    "js": js,
    "goto_url": goto_url,
    "wait_for_load": wait_for_load,
    "page_info": page_info,
    "fill_input": fill_input,
    "capture_screenshot": capture_screenshot,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("browser_exec runner: missing the script path\n")
        return 2
    script_path = argv[1]
    try:
        with open(script_path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except OSError as exc:
        sys.stderr.write(f"browser_exec runner: cannot read {script_path}: {exc}\n")
        return 2

    namespace: dict = {
        "__name__": "__browser_exec__",
        "__builtins__": __builtins__,
        "workspace": WORKSPACE,
    }
    namespace.update(_HELPERS)

    try:
        exec(compile(source, script_path, "exec"), namespace)  # noqa: S102 - 那段代码就是模型写的
    except Exception as exc:  # noqa: BLE001 - 失败要变成一句人话回给模型
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
