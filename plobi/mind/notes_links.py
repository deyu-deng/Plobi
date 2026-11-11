"""笔记之间的链接图（反向链接面板要的那一张）。

Obsidian 的右栏有一块「反向链接」：**哪几篇笔记链到了这一篇**。要算它，就得知道
全库每一篇正文里有哪些 ``[[…]]`` —— 前端拿不到（它一次只读一篇），所以这一层落在
后端：扫一遍 ``Notes`` 根，把每篇的出链解析出来，再倒一张反向表。

规则与前端那一套**对齐**（``apps/desktop/src/app/knowledge/wikilink.ts``）：

- 目标按**文件名**（不含扩展名）或 **frontmatter 里的 title** 匹配 —— Obsidian 是
  按文件名匹配，我们额外收 title，因为盘上笔记的标题常与文件名不同；
- 匹配不上的目标**原样保留**（Obsidian 会列出一个待创建项，而不是把这条链悄悄
  丢掉；屏上据此显示「未创建」）；
- 代码围栏里的 ``[[…]]`` 不算链接（那多半是示例）；
- 嵌图（``![[图.png]]``）不是链接。

**不许越界**：只读 ``Notes`` 根底下那棵树，不跟目录、不跟符号链接出去。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from .paths import real_target

# `[[目标|别名]]` —— 目标到 `|` 或 `#` 为止。
WIKILINK_RE = re.compile(r"\[\[([^\][\n]+)\]\]")

# 嵌图不是链接。
IMAGE_EXT = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|bmp|avif|ico)$", re.IGNORECASE)

NOTES_ROOT = "Notes"


def _frontmatter(text: str) -> dict[str, Any]:
    """frontmatter 那一圈；没有或坏了就当空的（不抛）。"""
    if not text.startswith("---"):
        return {}

    end = text.find("\n---", 3)

    if end < 0:
        return {}

    block = text[3:end].strip()

    if not block:
        return {}

    try:
        import yaml

        parsed = yaml.safe_load(block)
    except Exception:  # noqa: BLE001 — 一篇坏 frontmatter 不该让整张图塌掉
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _outgoing(text: str) -> list[str]:
    """正文里的出链目标（去重、保序）。

    代码围栏里的不算 —— 那多半是示例（盘上真有 `````md `` 里写 ``[[图.png]]`` 的）。
    """
    out: list[str] = []
    fence: str | None = None

    for line in text.splitlines():
        marker = line.strip()[:3]

        if fence is None:
            if marker in ("```", "~~~"):
                fence = marker
                continue
        else:
            if marker == fence:
                fence = None
            continue

        for match in WIKILINK_RE.finditer(line):
            raw = match.group(1).split("|")[0].split("#")[0].strip()

            if not raw or IMAGE_EXT.search(raw):
                continue
            if raw not in out:
                out.append(raw)

    return out


def _read(path: Path) -> str:
    """读一篇笔记；读不出来当空（坏文件不该让整张图塌掉）。"""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""


def build_link_graph(root: Optional[Path] = None) -> dict[str, Any]:
    """全库的链接图。

    形状::

        {
          "links":     {"Notes/数学/微积分.md": ["Notes/物理/大学物理.md", "未创建的东西"]},
          "backlinks": {"Notes/物理/大学物理.md": ["Notes/数学/微积分.md"]},
          "titles":    {"Notes/数学/微积分.md": "微积分"},
          "totals":    {"notes": 25, "links": 49},
        }

    ``links`` 里能解析上的目标是**逻辑路径**，解析不上的留原样（屏上标成「未创建」）。
    ``backlinks`` 只收**真的指向某一篇**的链接 —— 指向不存在目标的那些不进表
    （它们没有「被谁引用」的落点）。
    """
    base = real_target(NOTES_ROOT, root)

    if not base.is_dir():
        raise FileNotFoundError(f"notes root does not exist: {base}")

    titles: dict[str, str] = {}
    raw_links: dict[str, list[str]] = {}
    index: dict[str, str] = {}

    for path in sorted(base.rglob("*.md")):
        if not path.is_file():
            continue

        logical = f"{NOTES_ROOT}/{path.relative_to(base).as_posix()}"
        text = _read(path)
        front = _frontmatter(text)
        title = str(front.get("title") or path.stem).strip() or path.stem

        titles[logical] = title
        raw_links[logical] = _outgoing(text)

        stem = path.stem.strip().lower()

        if stem and stem not in index:
            index[stem] = logical
        if title and title.lower() not in index:
            index[title.lower()] = logical

    links: dict[str, list[str]] = {}
    backlinks: dict[str, list[str]] = {}

    for logical, targets in raw_links.items():
        resolved: list[str] = []

        for target in targets:
            path = index.get(target.lower())

            if path and path not in resolved:
                resolved.append(path)

            # 解析不上的原样留着（屏上显示成「未创建」），但**不进反向表**。
            if path is None and target not in resolved:
                resolved.append(target)

        links[logical] = resolved

    for source, targets in links.items():
        for target in targets:
            if target not in titles:
                continue
            bucket = backlinks.setdefault(target, [])
            if source not in bucket:
                bucket.append(source)

    total_links = sum(len(targets) for targets in links.values())

    return {
        "backlinks": backlinks,
        "links": links,
        "titles": titles,
        "totals": {"links": total_links, "notes": len(titles)},
    }
