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

**16b 补进来的那一组**（旧那十几把点击类工具的能力，一条一条搬过来）：
``snapshot()``／``click(target)``／``press(key)``／``scroll(direction)``／``back()``／
``get_images()``／``console_logs()``／``dialogs()``／``respond_dialog(action, text)``／
``frames()``。

**这一版的边界**：helper 作用在**当前页**（supervisor 跟的就是第一个页面会话）
⇒ 多标签接管（``new_tab`` 切页）属 16c。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List

_BRIDGE_URL = os.environ.get("PLOBI_BROWSER_BRIDGE_URL", "").rstrip("/")
_TOKEN = os.environ.get("PLOBI_BROWSER_BRIDGE_TOKEN", "")
WORKSPACE = os.environ.get("PLOBI_BROWSER_WORKSPACE", "")

# ``snapshot()`` 给的 ref → 定位信息。**只在这一次调用内有效**（Python 变量不持久，
# 子进程也是一次调用一个 ⇒ 跨调用要重新 snapshot）。
_REFS: Dict[str, Dict[str, Any]] = {}


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
    """导航当前页到 ``url``（**带 URL 护栏**），等 load 完，回最终地址。

    护栏判在**主进程**那一侧：云元数据那条地板线一律拒；私有／内网地址在浏览器是
    **远端**时拒（本机浏览器放行——本机开发服务器是正常用法）。落点还会**复核**一次
    （跳转也算）；复核不过就把页面打回空白页并报错。
    """
    import time

    _bridge("/navigate", {"url": str(url)})
    time.sleep(0.3)  # 让新页真的开始加载，再等 load 完
    wait_for_load()
    final = str(js("window.location.href") or url)
    verdict = _bridge("/check_url", {"url": final}) or {}
    if not verdict.get("safe", True):
        try:
            cdp("Page.navigate", url="about:blank")
        except Exception:  # noqa: BLE001 - 打回空白页失败也别盖掉真正的原因
            pass
        raise RuntimeError(
            str(verdict.get("reason") or "Blocked: the page landed somewhere unsafe")
        )
    return final


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


# ── 页面元素：找、点、按键 ───────────────────────────────────────────────────


_INTERACTIVE_SELECTORS = (
    'a[href],button,input,select,textarea,summary,'
    '[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="tab"],'
    '[role="menuitem"],[role="combobox"],[role="switch"],[role="option"],'
    "[onclick],[contenteditable=\"true\"]"
)

_CSS_PATH_JS = r"""
const cssPath = (el) => {
  const parts = [];
  let node = el;
  while (node && node.nodeType === 1 && parts.length < 10) {
    if (node.id && /^[A-Za-z][\w-]*$/.test(node.id)) { parts.unshift('#' + node.id); break; }
    let part = node.tagName.toLowerCase();
    const parent = node.parentElement;
    if (parent) {
      const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
    }
    parts.unshift(part);
    node = parent;
  }
  return parts.join(' > ');
};
"""


def _snapshot_js(limit: int) -> str:
    """可交互元素清单那段 JS（``limit`` 拼进去）。"""
    return (
        "(() => {"
        + _CSS_PATH_JS
        + "const out = []; let index = 0;"
        + "for (const el of document.querySelectorAll(%s)) {" % json.dumps(_INTERACTIVE_SELECTORS)
        + "const rect = el.getBoundingClientRect();"
        + "if (!rect.width && !rect.height) continue;"
        + "index += 1;"
        + "const label = (el.innerText || el.value || el.getAttribute('aria-label')"
        + " || el.getAttribute('placeholder') || el.getAttribute('title') || '')"
        + ".toString().trim().replace(/\\s+/g, ' ').slice(0, 120);"
        + "out.push({ref: '@e' + index, role: (el.getAttribute('role') || el.tagName.toLowerCase()),"
        + " text: label, selector: cssPath(el)});"
        + "if (out.length >= %d) break;" % int(limit)
        + "}"
        + "return out;"
        + "})()"
    )


def _rect_js(selector: str) -> str:
    """把元素滚到视野中间并回它中心点那段 JS。"""
    return (
        "(() => { const el = document.querySelector(%s); if (!el) return null;"
        % json.dumps(str(selector))
        + "el.scrollIntoView({block: 'center', inline: 'center'});"
        + "const rect = el.getBoundingClientRect();"
        + "if (!rect.width && !rect.height) return null;"
        + "return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};"
        + "})()"
    )


def snapshot(limit: int = 200) -> List[dict]:
    """当前页的**可交互元素**清单（角色／文字／定位方式）。

    每项带一个 ``@eN`` 的 ref；``click()`` 认这个 ref，也认裸的 CSS 选择器。
    """
    items = js(_snapshot_js(limit)) or []
    if not isinstance(items, list):
        return []
    _REFS.clear()
    for item in items:
        if isinstance(item, dict) and item.get("ref"):
            _REFS[str(item["ref"])] = item
    return items


def click(target: str) -> bool:
    """点一个元素。``target`` 是 ``snapshot()`` 给的 ``@e1``，或一条 CSS 选择器。

    走的是**真的鼠标事件**（先滚到视野中间，再派 mousePressed／mouseReleased），
    不是 JS 的 ``el.click()``——后者页面的框架常常不认。
    """
    selector = str(target or "").strip()
    if not selector:
        raise RuntimeError("browser_exec: click() needs a target (an @e ref or a CSS selector)")
    ref = _REFS.get(selector)
    if ref is not None:
        selector = str(ref.get("selector") or selector)
    rect = js(_rect_js(selector))
    if not isinstance(rect, dict):
        raise RuntimeError(f"browser_exec: no element matches {target!r}")
    x = float(rect.get("x") or 0.0)
    y = float(rect.get("y") or 0.0)
    cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
    cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
    return True


# 常用键 → CDP 的 key 名 ＋ 虚拟键码 ＋ 要不要带 text
_KEYS: Dict[str, tuple] = {
    "enter": ("Enter", 13, "\r"),
    "tab": ("Tab", 9, "\t"),
    "escape": ("Escape", 27, None),
    "esc": ("Escape", 27, None),
    "backspace": ("Backspace", 8, None),
    "delete": ("Delete", 46, None),
    "arrowup": ("ArrowUp", 38, None),
    "up": ("ArrowUp", 38, None),
    "arrowdown": ("ArrowDown", 40, None),
    "down": ("ArrowDown", 40, None),
    "arrowleft": ("ArrowLeft", 37, None),
    "left": ("ArrowLeft", 37, None),
    "arrowright": ("ArrowRight", 39, None),
    "right": ("ArrowRight", 39, None),
    "pageup": ("PageUp", 33, None),
    "pagedown": ("PageDown", 34, None),
    "home": ("Home", 36, None),
    "end": ("End", 35, None),
    "space": ("Space", 32, " "),
    "shift": ("Shift", 16, None),
    "control": ("Control", 17, None),
    "ctrl": ("Control", 17, None),
    "alt": ("Alt", 18, None),
}


def press(key: str) -> bool:
    """按一个键：``Enter``／``Tab``／``Escape``／``ArrowDown``…或单个字符。"""
    name = str(key or "").strip()
    if not name:
        raise RuntimeError("browser_exec: press() needs a key name")
    entry = _KEYS.get(name.lower())
    if entry is not None:
        cdp_key, key_code, text = entry
        params: Dict[str, Any] = {
            "key": cdp_key,
            "code": cdp_key,
            "windowsVirtualKeyCode": key_code,
            "nativeVirtualKeyCode": key_code,
        }
        if text:
            params["type"] = "keyDown"
            params["text"] = text
            params["unmodifiedText"] = text
        else:
            params["type"] = "rawKeyDown"
        cdp("Input.dispatchKeyEvent", **params)
        cdp(
            "Input.dispatchKeyEvent",
            type="keyUp",
            key=cdp_key,
            code=cdp_key,
            windowsVirtualKeyCode=key_code,
            nativeVirtualKeyCode=key_code,
        )
        return True
    if len(name) == 1:
        cdp("Input.dispatchKeyEvent", type="keyDown", key=name, text=name, unmodifiedText=name)
        cdp("Input.dispatchKeyEvent", type="keyUp", key=name)
        return True
    raise RuntimeError(f"browser_exec: unknown key {key!r}")


def scroll(direction: str = "down", amount: int = 600) -> bool:
    """滚页面。``direction``：``down``／``up``／``top``／``bottom``。"""
    where = str(direction or "down").strip().lower()
    if where == "top":
        js("window.scrollTo(0, 0); true")
    elif where == "bottom":
        js("window.scrollTo(0, document.body ? document.body.scrollHeight : 0); true")
    else:
        delta = int(amount) if where != "up" else -int(amount)
        js("window.scrollBy(0, %d); true" % delta)
    return True


def back() -> bool:
    """退回上一页（浏览器那个后退按钮）。"""
    js("history.back(); true")
    wait_for_load()
    return True


def get_images(limit: int = 50) -> List[str]:
    """当前页的图片地址列表。"""
    return (
        js(
            "Array.from(document.images).slice(0, %d)"
            ".map((i) => i.currentSrc || i.src).filter(Boolean)" % int(limit)
        )
        or []
    )


# ── 常驻会话那层的状态（弹窗／控制台／frame 树） ───────────────────────────


def _state() -> dict:
    state = _bridge("/state", {}) or {}
    return state if isinstance(state, dict) else {}


def console_logs(limit: int = 50) -> List[dict]:
    """页面的控制台／异常日志（常驻会话那层攒的，最近的在前）。"""
    events = _state().get("console_errors") or []
    if not isinstance(events, list):
        return []
    return events[-int(limit) :]


def dialogs() -> List[dict]:
    """当前开着的原生弹窗（alert／confirm／prompt）。"""
    pending = _state().get("pending_dialogs") or []
    return pending if isinstance(pending, list) else []


def respond_dialog(action: str, text: str = "") -> dict:
    """处理一个开着的弹窗：``accept`` 或 ``dismiss``（prompt 可以带 ``text``）。"""
    return _bridge("/dialog", {"action": str(action), "text": str(text)}) or {}


def frames() -> dict:
    """页面的 frame 树（子框架清单）。"""
    tree = _state().get("frame_tree") or {}
    return tree if isinstance(tree, dict) else {}


_HELPERS = {
    "cdp": cdp,
    "js": js,
    "goto_url": goto_url,
    "wait_for_load": wait_for_load,
    "page_info": page_info,
    "fill_input": fill_input,
    "capture_screenshot": capture_screenshot,
    "snapshot": snapshot,
    "click": click,
    "press": press,
    "scroll": scroll,
    "back": back,
    "get_images": get_images,
    "console_logs": console_logs,
    "dialogs": dialogs,
    "respond_dialog": respond_dialog,
    "frames": frames,
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
