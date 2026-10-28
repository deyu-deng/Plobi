"""把侧车已经解好的那几件搬进 Plobi 的家（`WP-WECHAT-MEDIA`）。

分工要说清：**解密从来不在我们这侧**。chatlog 侧车拿数据密钥解微信库，`/image/{md5}` 那几只口
返回的已经是真的图/视频/语音（`.dat` 它自己解，语音它顺手 silk→mp3）。这一档只做三件事：
按 `media_of` 交出来的键去取字节、落到自己家、封顶并说清放弃了几件。

落点是 `PLOBI_HOME/plobi/wechat-media/<kind>/<key><后缀>`：
① **不落 C 盘、不在微信的目录里留第二份**（D 盘规矩：运行数据禁止残留 C 盘）；
② 文件名只用那一串的 **md5**，不用消息里带的任何标题/路径——那是要落进别人机器的字符串，
   让它决定文件名就是送一个路径穿越的口（`_safe_key` 因此只放过字母数字与 `-`/`_`）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .client import ChatlogUnavailable

#: 单件上限与一轮上限：到顶就报数，不静默截断（那条"成功但空货"的反面教训）。
MAX_MEDIA_BYTES = 50 * 1024 * 1024
MAX_MEDIA_PER_ROUND = 40

#: 内容类型 → 后缀。引擎认得的原样留着，认不出来的按 kind 兜一个，再不行无后缀。
_SUFFIX_BY_CONTENT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/silk": ".slk",
    "application/pdf": ".pdf",
}
_SUFFIX_BY_KIND = {"image": ".jpg", "video": ".mp4", "voice": ".mp3", "file": ""}

_KEY_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,127}$")


def media_dir() -> Path:
    override = (os.environ.get("PLOBI_WECHAT_MEDIA_DIR") or "").strip()
    if override:
        return Path(override)
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "plobi" / "wechat-media"


def _safe_key(key: str) -> str:
    return str(key or "").strip()


def stage_media(client, items: list[dict], *, into: Optional[Path] = None,
                budget: int = MAX_MEDIA_BYTES, max_files: int = MAX_MEDIA_PER_ROUND
                ) -> tuple[list[dict], int, int, int]:
    """取回这一轮的媒体件，返回 `(取回的, 认不出的键, 失败的, 放弃的)`。

    四个数分开是有意的：**"这键不像键"、"侧车说没有这件"、"太大不取"是三件不同的事**，
    合并成一个"跳过 N 件"就读不出该修哪一个。
    """
    root = into or media_dir()
    saved: list[dict] = []
    malformed = failed = dropped = 0

    for item in items:
        kind = str(item.get("kind") or "")
        key = _safe_key(item.get("key") or "")

        if not _KEY_SHAPE.match(key) or kind not in _SUFFIX_BY_KIND:
            malformed += 1
            continue

        target_dir = root / kind
        target = next(iter(sorted(target_dir.glob(f"{key}*"))), None)
        if target is not None and target.stat().st_size > 0:
            saved.append({**item, "saved_to": str(target), "size_bytes": target.stat().st_size, "reused": True})
            continue

        if len(saved) >= max_files or budget <= 0:
            dropped += 1
            continue

        try:
            payload, content_type = client.media_bytes(kind, key)
        except ChatlogUnavailable:
            failed += 1
            continue

        if not payload:
            failed += 1
            continue
        if len(payload) > budget:
            dropped += 1
            continue
        budget -= len(payload)

        suffix = _SUFFIX_BY_CONTENT.get(content_type.split(";")[0].strip().lower()) or _SUFFIX_BY_KIND[kind]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{key}{suffix}"
        try:
            target.write_bytes(payload)
        except OSError:
            failed += 1
            continue

        saved.append({**item, "saved_to": str(target), "size_bytes": len(payload),
                      "content_type": content_type, "reused": False})

    return saved, malformed, failed, dropped
