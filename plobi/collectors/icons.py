r"""一条信源在屏上那个小方块 —— **从它自己声明的那个网站抓一次图标**（裁定 194 那条通用性的尾巴）。

同一只模块现在管**两样**都从那个站抓回来的东西：图标，和那个站**自报的名字**（用户 10-24：
「网站就显示网站名就行了，不要叫什么"教务"」）。名字与图标共用一次首页请求、共用一个缓存目录、
共用"抓不到就记 miss、不许阻塞那一屏"那三条规矩 —— 因为它们本来就是同一次访问的两个产物。

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
import html
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


#: 站名那一格的缓存后缀（与图标 `.bin` / `.miss` 同目录、同一套"抓过没有"的写法）。
_NAME_SUFFIX = ".name"
_NAME_MISS = ".name.miss"
#: 首页只读这么多就够拿到 `<title>` / `og:site_name`；再多是把别人整站拖进本机缓存。
_PAGE_BYTES = 64 * 1024
#: 一个专名不该是一句营销话。「钉钉，AI时代工作方式」→「钉钉」，「X - Login」→「X」。
_NAME_CUT = ("|", "｜", "—", "–", "-", "·", "•", "，", ",", "。", ":", "：")
_NAME_MAX = 32


def _name_path(source_id: str):
    return icon_dir() / f"{_safe_name(source_id)}{_NAME_SUFFIX}"


def cached_site_name(source_id: str) -> Optional[str]:
    """盘上有就给；有 miss 标记回 ``""``（**抓过、那上面没有名字**）；都没有回 ``None``（没抓过）。"""
    path = _name_path(source_id)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    if (icon_dir() / f"{_safe_name(source_id)}{_NAME_MISS}").exists():
        return ""
    return None


def site_name(source_id: str, site: str, opener=urllib.request.urlopen) -> str:
    """那个站**自己报**的名字（`og:site_name` → `<title>`），抓不到就是空串。

    与 :func:`icon_data_url` 同一族三条：只走**声明**的那个站、只抓一次并落盘、
    **永不抛**（名字是装饰性的那一半，它不该把那一屏弄红——第三方包没填 `display_name`
    而站又打不开时，界面回它自己的语言目录，最后才是「暂未适配」那一句）。
    """
    if not site or not site.strip():
        return ""
    hit = cached_site_name(source_id)
    if hit is not None:
        return hit

    try:
        name = _name_from(_read_page(site, opener))
    except Exception as exc:  # noqa: BLE001 - 站方挂了/超时/证书/404 全都是"它没报名字"
        logger.debug("source site name %s: %s: %s", source_id, type(exc).__name__, exc)
        name = ""

    icon_dir().mkdir(parents=True, exist_ok=True)
    if name:
        _name_path(source_id).write_text(name, encoding="utf-8")
        (icon_dir() / f"{_safe_name(source_id)}{_NAME_MISS}").unlink(missing_ok=True)
        return name
    (icon_dir() / f"{_safe_name(source_id)}{_NAME_MISS}").write_text("", encoding="utf-8")
    return ""


def site_name_now(source_id: str, site: str) -> str:
    """给那一屏用的那一格：盘上有就给，没有就**后台去抓**并立刻回空串（与 :func:`icon_now` 同形）。"""
    hit = cached_site_name(source_id)
    if hit is not None:
        return hit
    if not site:
        return ""

    import threading

    threading.Thread(target=site_name, args=(source_id, site), daemon=True).start()

    return ""


def _read_page(site: str, opener, *, hops: int = 2) -> str:
    """取那个站的首页正文，**跟一次 `<meta http-equiv=refresh>`**。

    这一跳不是洁癖：教务那家（`zdbk.zju.edu.cn`）的根目录现读就是一行 68 字节的
    `<meta refresh content="1; url=/jwglxt">`，不跟这一跳就永远读不到 `<title>`，
    而那一个 title（「本科教学管理信息服务平台」）正是用户要的"网站名"。
    """
    url = site if re.match(r"^https?://", site) else f"https://{site}"
    for _ in range(max(hops, 1)):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return ""
        if not parts.path:
            # 声明里通常只写到 origin（`https://zdbk.zju.edu.cn`）；补一个 "/" 再打，
            # 不然那一跳的相对目标（`/jwglxt`）没有基准可解，站方给不给 308 是它的心情。
            url = f"{parts.scheme}://{parts.netloc}/"
            parts = urllib.parse.urlsplit(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=TIMEOUT_SECONDS) as reply:
            text = reply.read(_PAGE_BYTES).decode("utf-8", "replace")
        nxt = _refresh_target(url, text)
        if not nxt:
            return text
        url = nxt
    return text


def _refresh_target(base_url: str, page: str) -> Optional[str]:
    found = re.search(r"""<meta[^>]*?http-equiv=["']?refresh["']?[^>]*?>""", page, re.I)
    if not found:
        return None
    content = re.search(r"""content=["']\s*\d+\s*;\s*url=([^"'>\s]+)""", found.group(0), re.I)
    if not content:
        return None
    target = urllib.parse.urljoin(base_url, content.group(1).strip())
    parts = urllib.parse.urlsplit(target)
    # 只跟**同一个站**那一跳：把"抓个名字"变成"被别人的页面牵着走"是没必要的一张票。
    if parts.scheme not in ("http", "https") or parts.netloc != urllib.parse.urlsplit(base_url).netloc:
        return None
    return target


def _name_from(page: str) -> str:
    """从一段 HTML 里挑出那个站自己的名字；挑不出来回空串。"""
    if not page:
        return ""

    og = re.search(
        r"""<meta[^>]+(?:property|name)=["'](?:og:site_name|twitter:title)["'][^>]*>""", page, re.I
    )
    if og:
        content = re.search(r"""content=["']([^"']+)["']""", og.group(0), re.I)
        if content and content.group(1).strip():
            return _shorten(content.group(1))

    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    if title:
        return _shorten(title.group(1))

    return ""


def _shorten(raw: str) -> str:
    """收成一个**专名**：切掉分隔符后面的那句话、实体还原、空白归一、限长。"""
    text = html.unescape(raw or "")
    head = text
    for marker in _NAME_CUT:
        if marker in head:
            head = head.split(marker, 1)[0]
    head = re.sub(r"\s+", " ", head).strip()
    return head[:_NAME_MAX].strip()


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
