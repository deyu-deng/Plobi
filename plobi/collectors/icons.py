r"""一条信源在屏上那个小方块 —— **从它自己声明的那个网站抓一次图标**（裁定 194 那条通用性的尾巴）。

为什么在这儿抓而不是前端硬编一张表：那张表已经因为少一个 `zdbk` 在屏上画成了
「没记到名字的信源」——**加一条信源要顺手改前端**这件事，正是信源层一直要消灭的东西
（`specs/source-layer.md` §8 有一条验收就是这个）。所以图标走的是声明：`SourceSpec.site`
有值就去那个站抓一次，没有就退前端那张品牌表，再退首字母。

三条不是洁癖的规矩：

1. **只抓一次，落在盘上。** 每次开设置页都去请求一遍外部站点，等于让"看一眼界面"变成
   一次对第三方服务器的轮询。抓不到也记一笔 miss——否则每次开页都重走一遍超时，
   那一屏就会转圈（用户看到的"卡"就是这么来的）。
2. **只走声明的站，不走去用户填的那格。** `site` 是代码里声明的；用户在首次配置里填的
   地址（`base_url` 那一格）**不是**图标来源——那是一条把"填个地址"变成"让本机去访问任意主机"
   的路，而图标不值得开这个口。
3. **值以 data URL 进 payload，不开一条静态资源路由。** 那条路由要么带鉴权（`<img>` 带不了
   header）、要么不带（本机任意进程可读）——两个都比"多几 KB JSON"贵。

落点：`$PLOBI_HOME/plobi/source-icons/`（与其余运行数据同盘，`D:\_POLICY` 那条"禁止残留 C 盘"
一并遵守；测试用 `PLOBI_SOURCE_ICON_DIR` 钉进 tmp）。
"""

from __future__ import annotations

import base64
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: 一次抓取的上限。图标本来就该是几 KB；超了就当没抓到，不把别人的大图塞进 payload。
MAX_BYTES = 64 * 1024
TIMEOUT_SECONDS = 2.0
USER_AGENT = "Plobi-Desktop/1.0 (local source icon; contact: this machine only)"
_MISS = ".miss"
#: 魔数 → MIME。认不出来就当没有：`<img>` 拿到一个错 MIME 比拿到 null 更难查。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
)


def icon_dir() -> Path:
    override = (os.environ.get("PLOBI_SOURCE_ICON_DIR") or "").strip()
    if override:
        return Path(override)
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "plobi" / "source-icons"


def _safe_name(source_id: str) -> str:
    """文件名只留 `[A-Za-z0-9._-]`——`source_id` 是包名形状，但这是防御性写法而不是信任。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", source_id.strip())[:64] or "unnamed"


def cached(source_id: str) -> Optional[str]:
    """盘上已经有就回 data URL；有 miss 标记回 ``""``（**抓过、没有**）；都没有回 ``None``（没抓过）。"""
    path = icon_dir() / f"{_safe_name(source_id)}.bin"
    if path.is_file():
        mime = _mime_of(path.read_bytes()[:8])
        if mime:
            return _data_url(mime, path.read_bytes())
        return ""
    if (icon_dir() / f"{_safe_name(source_id)}{_MISS}").exists():
        return ""
    return None


def icon_now(source_id: str, site: str) -> str:
    """**给那一屏用的那一格**：盘上有就给，没有就立刻回空串并**在后台去抓**。

    为什么不许同步抓：设置页一次要问四条源，一条 2 秒超时的话最坏是 8 秒白屏——
    "看一眼图标"不该把那一屏卡住。第一次打开就是没有图标（退品牌表/首字母），
    下一次就有了；抓取失败也不重排，miss 记在盘上。
    """
    hit = cached(source_id)
    if hit is not None:
        return hit
    if not site:
        return ""

    import threading

    threading.Thread(target=icon_data_url, args=(source_id, site), daemon=True).start()

    return ""


def icon_data_url(source_id: str, site: str, opener=urllib.request.urlopen) -> str:
    """这一条的图标，抓不到就是空串。**这一格永不抛**：图标是装饰，它不该把那一屏弄红。"""
    if not site or not site.strip():
        return ""
    hit = cached(source_id)
    if hit is not None:
        return hit

    try:
        url = _resolve_icon_url(site, opener)
        blob = url and _fetch(url, opener)
    except Exception as exc:  # noqa: BLE001 - 站方挂了、超时、证书、404 全都是"没有图标"
        logger.debug("source icon %s: %s: %s", source_id, type(exc).__name__, exc)
        blob = None

    if not blob:
        _mark(source_id, _MISS)
        return ""
    mime = _mime_of(blob[:8])
    if not mime:
        _mark(source_id, _MISS)
        return ""
    target = icon_dir() / f"{_safe_name(source_id)}.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)
    (icon_dir() / f"{_safe_name(source_id)}{_MISS}").unlink(missing_ok=True)
    return _data_url(mime, blob)


def _resolve_icon_url(site: str, opener) -> Optional[str]:
    """先认 `<link rel="icon">`，没有就退 `<host>/favicon.ico`。"""
    root = site if re.match(r"^https?://", site) else f"https://{site}"
    parts = urllib.parse.urlsplit(root)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    base = f"{parts.scheme}://{parts.netloc}"
    try:
        with opener(root if not root.endswith("/") else root, timeout=TIMEOUT_SECONDS) as reply:
            head = reply.read(64 * 1024).decode("utf-8", "replace")
        found = re.search(r"""<link[^>]+rel=["']?(?:shortcut\s+)?icon["']?[^>]+>""", head, re.I)
        href = re.search(r"""href=["']([^"']+)["']""", found.group(0), re.I) if found else None
        if href:
            return urllib.parse.urljoin(base, href.group(1).strip())
    except Exception:  # noqa: BLE001 - 首页读不到不拦 favicon 这一条退路
        pass
    return f"{base}/favicon.ico"


def _fetch(url: str, opener) -> Optional[bytes]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    with opener(request, timeout=TIMEOUT_SECONDS) as reply:
        blob = reply.read(MAX_BYTES + 1)
    return blob if len(blob) <= MAX_BYTES else None


def _mime_of(header: bytes) -> str:
    for magic, mime in _MAGIC:
        if header.startswith(magic):
            return mime
    if header.lstrip()[:5].lower().startswith(b"<svg"):
        return "image/svg+xml"
    return ""


def _data_url(mime: str, blob: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"


def _mark(source_id: str, suffix: str) -> None:
    icon_dir().mkdir(parents=True, exist_ok=True)
    (icon_dir() / f"{_safe_name(source_id)}{suffix}").write_text("", encoding="utf-8")
