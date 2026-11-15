"""``browser_exec`` 的 **URL 护栏**（``WP-BROWSER-EXEC` 判据 4）。

旧族那把 ``browser_navigate`` 的护栏写在 ``tools/browser_tool.py`` 里（那一族 16b 起
不再注册，**但护栏本身是对的**）⇒ 这一块直接复用 ``tools.url_safety`` ＋
``tools.website_policy``，只把判定收成新族需要的形状：

* **云元数据那条地板线**（IMDS／ECS task metadata／Azure wire server）——**无条件拒**，
  浏览器在**本机**也一样拒（本机 Chromium 跑在云主机上时照样能摸到宿主机的 IAM 凭据）。
* **私有／内网地址**——浏览器在**远端**（``/browser connect`` 接到别人的机器，或将来的
  云 provider）时拒；浏览器在**本机**（我们自己起的那一个）时放行——本机开发服务器
  （``localhost:3000`` 之类）是正常用法。
* **站点策略**（用户自己配的屏蔽名单）——一律照旧。

**失败即闭**：护栏模块导不进来 ⇒ 一律拒（与旧族同一形状：不 fail-open）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from tools.url_safety import (
        is_always_blocked_url,
        is_safe_url,
        normalize_url_for_request,
        sensitive_query_param_name,
    )
except Exception:  # noqa: BLE001 - 护栏导不进来就一律拒（fail-closed）
    logger.warning("browser_exec guard: url_safety unavailable — blocking every URL")
    is_always_blocked_url = None  # type: ignore[assignment]
    is_safe_url = None  # type: ignore[assignment]
    normalize_url_for_request = None  # type: ignore[assignment]
    sensitive_query_param_name = None  # type: ignore[assignment]

try:
    from tools.website_policy import check_website_access
except Exception:  # noqa: BLE001 - 策略模块不可用 ⇒ 不拦（网站策略是用户自己的规矩）
    logger.warning("browser_exec guard: website_policy unavailable — skipping that policy")
    check_website_access = None  # type: ignore[assignment]


@dataclass(frozen=True)
class UrlVerdict:
    """一次 URL 判定的结果：``ok`` 之外带一句能直接回给模型的话。"""

    ok: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"safe": self.ok, "reason": self.reason}


def normalize(url: str) -> str:
    """把 URL 规整成能安全发出去的样子（ASCII 化、补 scheme 等）。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if normalize_url_for_request is None:
        return raw
    try:
        return normalize_url_for_request(raw)
    except Exception as exc:  # noqa: BLE001 - 规整失败就按原样交给后面的判定
        logger.debug("browser_exec guard: normalize failed for %r: %s", raw, exc)
        return raw


def check_navigation_url(url: str, *, browser_is_local: bool) -> UrlVerdict:
    """这一步能不能导航到 ``url``。

    ``browser_is_local``：浏览器跑在**本机**（我们自己起的那一个）还是**远端**
    （接的是别人的机器／将来的云 provider）。远端不许碰私有地址；本机可以。
    """
    raw = str(url or "").strip()
    if not raw:
        return UrlVerdict(False, "Blocked: no URL given")

    if is_always_blocked_url is None:
        return UrlVerdict(False, "Blocked: the URL safety module is unavailable")

    normalized = normalize(raw)

    if is_always_blocked_url(normalized):
        return UrlVerdict(False, "Blocked: URL targets a cloud metadata endpoint")

    if not browser_is_local:
        if sensitive_query_param_name is not None:
            key = sensitive_query_param_name(normalized)
            if key:
                return UrlVerdict(
                    False,
                    "Blocked: URL contains a credential-like query parameter "
                    f"({key}). A remote browser backend is a third-party reader; "
                    "use a local browser session or drop that parameter.",
                )
        if is_safe_url is not None and not is_safe_url(normalized):
            return UrlVerdict(False, "Blocked: URL targets a private or internal address")

    if check_website_access is not None:
        try:
            blocked = check_website_access(normalized)
        except Exception as exc:  # noqa: BLE001 - 策略模块自己出错不该拦住导航
            logger.debug("browser_exec guard: website policy check failed: %s", exc)
            blocked = None
        if blocked:
            return UrlVerdict(
                False,
                str(blocked.get("message") or "Blocked by the website access policy"),
            )

    return UrlVerdict(True, "")
