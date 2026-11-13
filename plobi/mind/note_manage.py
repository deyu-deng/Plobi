"""笔记的改名与删除（WP-NOTE-RENAME-DELETE 的后端那一半）。

两件事都是**人**的动作，而且都坏不起：

- **改名**：文件名、frontmatter 里的 ``title``、正文第一个一级标题、还有别的笔记里
  指向它的 ``[[双链]]``，得一起改。少改一处，那篇就断链了。
- **删除**：不许 ``unlink`` 掉就完事——移进 ``<人读面>/.trash/``，还能捞回来。

两件事**永不自动**（用户 2026-10-23：日记＋画像＋删除）⇒ :data:`NEVER_AUTO_KINDS`。
Agent 那一侧只许把算好的计划（:class:`NoteOp`）存进待批队列，批了才落盘。

路径一律走 :func:`plobi.mind.paths.real_target`，落点必须在人读面 ``notes/`` 之内；
判据复用 :func:`plobi.mind.notes_library._safe_note_target`（读与写共用同一份——
两处各写一套，早晚漂成「读得到但写不进去」）。

落盘顺序：**先写后删**——新文件与各入链笔记都原子写好之后，才动旧文件。
中途失败时旧文件还在。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import yaml

from . import notes_library
from .notes_library import NoteConflict
from .paths import normalize_logical, real_target

logger = logging.getLogger(__name__)

RENAME = "rename"
DELETE = "delete"

# 永不自动：这两样没有自动档（用户 2026-10-23：日记＋画像＋删除）。
NEVER_AUTO_KINDS = (RENAME, DELETE)

# 回收站：人读面根底下的 ``.trash/``。那个仓的 .gitignore 用 ``/*`` 挡住了它，
# 所以删掉的文件不进 git，也不许被 ``git add``（加了 git 会直接报错）。
TRASH_DIR = ".trash"

NOTES_LOGICAL_ROOT = notes_library.NOTES_LOGICAL_ROOT


# ── 载荷（待批队列里存的就是它）────────────────────────────────────────────────


@dataclass(frozen=True)
class FileChange:
    """一档要写回去的文件：逻辑路径 + 新内容。"""

    path: str
    text: str

    def to_json(self) -> dict[str, str]:
        return {"path": self.path, "text": self.text}

    @classmethod
    def from_json(cls, blob: Any) -> "FileChange":
        if not isinstance(blob, dict):
            raise ValueError("a file change must be a JSON object")
        return cls(path=str(blob.get("path") or ""), text=str(blob.get("text") or ""))


@dataclass(frozen=True)
class NoteOp:
    """一次改名或删除的**计划**：只算不落。

    ``shas`` 是改之前各档的 sha256——落盘前先比一次，人在这期间改过就拒。
    """

    kind: str
    path: str
    new_path: str = ""
    new_title: str = ""
    changes: tuple[FileChange, ...] = ()
    trash: str = ""
    shas: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "path": self.path,
                "new_path": self.new_path,
                "new_title": self.new_title,
                "changes": [change.to_json() for change in self.changes],
                "trash": self.trash,
                "shas": [[path, sha] for path, sha in self.shas],
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, blob: str) -> "NoteOp":
        try:
            data = json.loads(blob)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"the note op payload is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("the note op payload must be a JSON object")
        kind = str(data.get("kind") or "")
        if kind not in (RENAME, DELETE):
            raise ValueError(f"unknown note op kind: {kind!r}")
        changes = tuple(
            FileChange.from_json(item) for item in (data.get("changes") or [])
        )
        shas = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in (data.get("shas") or [])
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        )
        return cls(
            kind=kind,
            path=str(data.get("path") or ""),
            new_path=str(data.get("new_path") or ""),
            new_title=str(data.get("new_title") or ""),
            changes=changes,
            trash=str(data.get("trash") or ""),
            shas=shas,
        )


# ── 路径与落点 ────────────────────────────────────────────────────────────────


def _notes_root() -> Path:
    """``Notes`` 落到盘上的那一格（走 real_target，不手拼路径）。"""
    return real_target(NOTES_LOGICAL_ROOT)


def _human_root() -> Path:
    """人读面本体（``notes/`` 的上一层）——回收站 ``.trash/`` 落在它下面。"""
    return _notes_root().parent


def _canonical_logical(path: str) -> str:
    """进来的路径先归一（只认 ``Notes/…``）。"""
    return normalize_logical(str(path or "").strip().replace("\\", "/"))


def _logical_of(path: Path, root: Path) -> str:
    """盘上的文件 → 逻辑路径（``Notes/…``）。"""
    return "/".join((NOTES_LOGICAL_ROOT,) + path.relative_to(root).parts)


def _relative_of(path: Path, root: Path) -> str:
    """盘上的文件 → 相对 notes 根的路径（git 的 pathspec 用这个）。"""
    return path.relative_to(root).as_posix()


def _sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_text(path: Path) -> str:
    """按字节读进来再解 UTF-8。

    不走文本模式：文本模式会把 ``\\r\\n`` 变成 ``\\n``，那一改就是「其余字节
    一个不动」这条规矩的破口。解不开（不是 UTF-8）就拒，不拿替换字符糊过去。
    """
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"note {path.name!r} is not UTF-8 text: {exc}") from exc


def classify_note(path: str) -> str:
    """这篇现在是什么状态。

    - ``ok`` —— 在盘上；
    - ``missing`` —— 路径合法，但盘上没有这一篇；
    - ``invalid`` —— 越界、不是 ``.md``、或路径本身不合法。

    HTTP 那面要把「没有」与「路径不对」分开答（404 与 400），所以单问一句。
    """
    target = notes_library._safe_note_target(_canonical_logical(path))
    if target is None:
        return "invalid"
    return "ok" if target.is_file() else "missing"


# ── 正文的三处同步 ────────────────────────────────────────────────────────────

_TITLE_LINE_RE = re.compile(r"^([ \t]*title[ \t]*:[ \t]*)(.*)$")
_LINK_RE = re.compile(r"\[\[([^\[\]\n]*)\]\]")


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _split_frontmatter(text: str) -> tuple[str, str, str]:
    """切成 (frontmatter 块, 分隔符, 正文)。

    没有 frontmatter 时三样都是空串。判据与
    :func:`plobi.mind.notes_library._frontmatter` 同一份：``---`` 开头、
    后面还有一个 ``---`` 行才算。
    """
    if not text.startswith("---"):
        return "", "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", "", text
    return text[3:end], text[end : end + 4], text[end + 4 :]


def _yaml_scalar(value: str) -> str:
    """一个能安全进 YAML 的标量（引号由 yaml 自己决定，别手拼）。

    借一个 mapping 出手：直接 dump 一个标量，PyYAML 会在后面补一行文档结束符
    ``...``（根节点是标量时的老规矩），那行混进 frontmatter 就是脏的。
    """
    dumped = yaml.safe_dump(
        {"k": value}, allow_unicode=True, default_flow_style=False, width=1 << 20
    )
    first = dumped.splitlines()[0] if dumped.splitlines() else ""
    return first.split(":", 1)[1].strip() if ":" in first else value


def _split_yaml_comment(value: str) -> tuple[str, str]:
    """把 ``值  # 备注`` 拆成（值，备注）。

    YAML 的行尾注释是 ``#`` 前面有空白、且不在引号里。改名时那句备注**不许丢**——
    它是用户自己写在那儿的话。
    """
    in_single = in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1] in " \t":
                return value[:index].rstrip(), value[index:]
    return value.rstrip(), ""


def _rewrite_frontmatter_title(block: str, new_title: str) -> str:
    """把 frontmatter 里那一行 ``title:`` 的值换掉。

    没有 ``title`` 这一格就**不加**——不替用户造结构。
    行尾那句 ``# 备注`` 原样留着（那是用户写在那儿的话）。
    """
    lines = block.splitlines(keepends=True)
    for index, line in enumerate(lines):
        content, ending = _split_line_ending(line)
        match = _TITLE_LINE_RE.match(content)
        if match:
            _value, comment = _split_yaml_comment(match.group(2))
            tail = (" " + comment) if comment else ""
            lines[index] = match.group(1) + _yaml_scalar(new_title) + tail + ending
            return "".join(lines)
    return block


def _outside_fence_spans(text: str) -> list[tuple[int, int]]:
    """不在围栏代码块里的那些片段（按字符下标）。

    讲 Markdown／Obsidian 的笔记会在 ``` 里示范 ``[[双链]]``——按行扫会把
    示例当正文改掉。
    """
    spans: list[tuple[int, int]] = []
    fence: str | None = None
    seg_start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = line.strip()[:3]
        if fence is None:
            if marker in ("```", "~~~"):
                if offset > seg_start:
                    spans.append((seg_start, offset))
                fence = marker
                seg_start = offset + len(line)
        elif marker == fence:
            fence = None
            seg_start = offset + len(line)
        offset += len(line)
    if fence is None and offset > seg_start:
        spans.append((seg_start, offset))
    return spans


def _rewrite_first_heading(body: str, new_title: str) -> str:
    """正文里**第一个**一级标题换成新标题；围栏里的 ``#`` 不算。

    没有一级标题就不加（人自己写的笔记可以没有）。
    """
    fence: str | None = None
    out: list[str] = []
    done = False
    for line in body.splitlines(keepends=True):
        if not done:
            stripped = line.strip()
            marker = stripped[:3]
            if fence is None:
                if marker in ("```", "~~~"):
                    fence = marker
                elif stripped.startswith("# "):
                    indent = line[: len(line) - len(line.lstrip())]
                    _, ending = _split_line_ending(line)
                    out.append(f"{indent}# {new_title}{ending}")
                    done = True
                    continue
            elif marker == fence:
                fence = None
        out.append(line)
    return "".join(out)


def _rewrite_title_fields(text: str, new_title: str) -> str:
    """frontmatter 的 ``title:`` ＋ 正文第一个一级标题，两处一起换成新标题。"""
    block, separator, body = _split_frontmatter(text)
    if not separator:
        # 没有 frontmatter：不替用户造结构，只改正文那一级标题。
        return _rewrite_first_heading(text, new_title)
    if block:
        block = _rewrite_frontmatter_title(block, new_title)
    return "---" + block + separator + _rewrite_first_heading(body, new_title)


def _inline_code_spans(text: str) -> list[tuple[int, int]]:
    """行内代码（``code``）那些片段——按字符下标。

    Obsidian 讲双链的那几篇会拿行内代码示范 ``[[双链]]``（真库里有 5 处），
    而 Obsidian 自己**不把行内代码当链接** ⇒ 改名字时也不该动它。
    Markdown 的规则：成对的同长反引号串包起来的那一段就是行内代码。
    """
    spans: list[tuple[int, int]] = []
    index = 0
    size = len(text)
    while index < size:
        if text[index] != "`":
            index += 1
            continue
        opener_end = index
        while opener_end < size and text[opener_end] == "`":
            opener_end += 1
        width = opener_end - index
        cursor = opener_end
        closed = False
        while cursor < size:
            if text[cursor] != "`":
                cursor += 1
                continue
            closer_end = cursor
            while closer_end < size and text[closer_end] == "`":
                closer_end += 1
            if closer_end - cursor == width:
                spans.append((index, closer_end))
                index = closer_end
                closed = True
                break
            cursor = closer_end
        if not closed:
            # 没有配对的收尾 ⇒ 这一串不是行内代码，接着往后找。
            index = opener_end
    return spans


def _rewrite_outside_inline_code(chunk: str, targets: set[str], new_title: str) -> str:
    """只改**不在行内代码里**的那些双链。"""
    replace = lambda match: _rewrite_one_link(match.group(0), targets, new_title)  # noqa: E731
    spans = _inline_code_spans(chunk)
    if not spans:
        return _LINK_RE.sub(replace, chunk)
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(_LINK_RE.sub(replace, chunk[cursor:start]))
        pieces.append(chunk[start:end])
        cursor = end
    pieces.append(_LINK_RE.sub(replace, chunk[cursor:]))
    return "".join(pieces)


def _rewrite_links(text: str, targets: set[str], new_title: str) -> str:
    """把 ``[[X]]`` 里 X 命中 targets 的改成 ``[[新标题]]``。

    别名与小节锚点原样留着：``[[X|别名]]`` ⇒ ``[[新标题|别名]]``，
    ``[[X#小节]]`` ⇒ ``[[新标题#小节]]``。``[[#小节]]``（目标为空）不动；
    围栏代码块里的 ``[[…]]`` 不动。
    """
    if not targets:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in _outside_fence_spans(text):
        pieces.append(text[cursor:start])
        pieces.append(_rewrite_outside_inline_code(text[start:end], targets, new_title))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _rewrite_one_link(raw: str, targets: set[str], new_title: str) -> str:
    inner = raw[2:-2]
    target_part, sep, alias = inner.partition("|")
    base, hash_sep, anchor = target_part.partition("#")
    if base.strip() not in targets:
        return raw
    rebuilt = new_title + (hash_sep + anchor if hash_sep else "")
    if sep:
        rebuilt += sep + alias
    return "[[" + rebuilt + "]]"


def _iter_note_files(root: Path) -> list[Path]:
    """人读面 ``notes/`` 下面所有的 markdown（隐藏段跳过，与知识库扫描同一口径）。"""
    out: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(path)
    return out


def _check_new_name(new_name: str) -> str:
    """新名字：不带路径分隔符、不以 ``.`` 开头、一行之内。"""
    if not isinstance(new_name, str):
        raise ValueError("the new name must be a string")
    name = new_name.strip()
    if not name:
        raise ValueError("the new name is empty")
    if "/" in name or "\\" in name:
        raise ValueError(f"the new name must not contain a path separator: {new_name!r}")
    if name.startswith("."):
        raise ValueError(f"the new name must not start with a dot: {new_name!r}")
    if name.lower().endswith(".md"):
        # 契约里 ``name`` 是**不带扩展名**的名字。带了就拒——否则落成 ``x.md.md``。
        raise ValueError(f"the new name must not include the .md suffix: {new_name!r}")
    if any(char in name for char in "\r\n\t"):
        raise ValueError(f"the new name must be a single line: {new_name!r}")
    return name


# ── 算（只读盘）───────────────────────────────────────────────────────────────


def plan_rename(path: str, new_name: str) -> NoteOp:
    """算一次改名：只读盘，一个字节都不写。

    算出来的东西：新的文件名、frontmatter 的 ``title``、正文第一个一级标题，
    还有每一篇指向它的笔记该怎么改。``shas`` 记着改之前各档的 sha256。
    """
    logical = _canonical_logical(path)
    target = notes_library._safe_note_target(logical)
    if target is None:
        raise ValueError(
            f"note path is outside {NOTES_LOGICAL_ROOT}/ or is not a .md file: {path!r}"
        )
    if not target.is_file():
        raise ValueError(f"note {logical!r} does not exist under the notes root")

    name = _check_new_name(new_name)
    root = _notes_root().resolve()
    target = target.resolve()
    logical = _logical_of(target, root)

    parent_parts = target.relative_to(root).parent.parts
    new_logical = "/".join((NOTES_LOGICAL_ROOT,) + parent_parts + (name + ".md",))
    new_target = notes_library._safe_note_target(new_logical)
    if new_target is None:
        raise ValueError(f"the new path is not a valid note path: {new_logical!r}")
    if new_target.exists():
        raise ValueError(f"a note already exists at {new_logical!r}")

    old_text = _read_text(target)
    meta = notes_library._frontmatter(old_text)
    old_title = notes_library._title_of(old_text, target.stem, meta)
    targets = {old_title, target.stem} - {""}

    renamed = _rewrite_links(_rewrite_title_fields(old_text, name), targets, name)

    changes: list[FileChange] = [FileChange(path=new_logical, text=renamed)]
    shas: list[tuple[str, str]] = [(logical, _sha_of(target))]
    for other in _iter_note_files(root):
        if other.resolve() == target:
            continue
        text = _read_text(other)
        rewritten = _rewrite_links(text, targets, name)
        if rewritten == text:
            continue
        other_logical = _logical_of(other.resolve(), root)
        changes.append(FileChange(path=other_logical, text=rewritten))
        shas.append((other_logical, _sha_of(other)))

    return NoteOp(
        kind=RENAME,
        path=logical,
        new_path=new_logical,
        new_title=name,
        changes=tuple(changes),
        shas=tuple(shas),
    )


def plan_delete(path: str) -> NoteOp:
    """算一次删除：只读盘，一个字节都不写。

    落点写死在 ``<人读面>/.trash/<原逻辑路径>``——回收站里那份还能捞回来。
    """
    logical = _canonical_logical(path)
    target = notes_library._safe_note_target(logical)
    if target is None:
        raise ValueError(
            f"note path is outside {NOTES_LOGICAL_ROOT}/ or is not a .md file: {path!r}"
        )
    if not target.is_file():
        raise ValueError(f"note {logical!r} does not exist under the notes root")

    root = _notes_root().resolve()
    target = target.resolve()
    logical = _logical_of(target, root)
    return NoteOp(
        kind=DELETE,
        path=logical,
        trash=f"{TRASH_DIR}/{logical}",
        shas=((logical, _sha_of(target)),),
    )


# ── 落（写盘）─────────────────────────────────────────────────────────────────


def apply_op(op: NoteOp) -> dict[str, Any]:
    """把算好的计划落盘。"""
    if op.kind == RENAME:
        return _apply_rename(op)
    if op.kind == DELETE:
        return _apply_delete(op)
    raise ValueError(f"unknown note op kind: {op.kind!r}")


def rename_note(path: str, new_name: str) -> dict[str, Any]:
    """人侧改名：算 + 落。"""
    return apply_op(plan_rename(path, new_name))


def delete_note(path: str) -> dict[str, Any]:
    """人侧删除：算 + 落（移进回收站，可恢复）。"""
    return apply_op(plan_delete(path))


def _verify_shas(op: NoteOp) -> None:
    """盘上现状与计划时记下的对不上 ⇒ 拒。

    这条不许放宽：人在这期间改过这篇（或者已经把它删了），那就让人自己决定。
    """
    if op.path not in {logical for logical, _ in op.shas}:
        # 载荷是从待批队列里读回来的：没记下 sha 的计划不许落盘。
        raise ValueError(f"the op does not record the sha of {op.path!r}; refusing to write")
    for logical, expected in op.shas:
        target = notes_library._safe_note_target(logical)
        if target is None or not target.is_file():
            raise NoteConflict(f"笔记 {logical!r} 已经不在盘上了，先重新看一眼再动手")
        if _sha_of(target) != expected:
            raise NoteConflict(f"笔记 {logical!r} 在这期间被人改过了，先重新看一眼再动手")


def _atomic_write(path: Path, text: str) -> None:
    """原子写：临时文件 + ``os.replace``（与 ``notes_library.save_note`` 同一写法）。"""
    encoded = text.encode("utf-8")
    handle, tmp = tempfile.mkstemp(prefix=".note-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _timestamped(path: Path) -> Path:
    """回收站里已经有同名 ⇒ 加时间戳后缀（还撞就再排一个号）。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{stamp}-{counter}{path.suffix}")
        counter += 1
    return candidate


def _apply_rename(op: NoteOp) -> dict[str, Any]:
    root = _notes_root().resolve()
    old_target = notes_library._safe_note_target(op.path)
    if old_target is None:
        raise ValueError(
            f"note path is outside {NOTES_LOGICAL_ROOT}/ or is not a .md file: {op.path!r}"
        )
    new_target = notes_library._safe_note_target(op.new_path)
    if new_target is None:
        raise ValueError(f"the new path is not a valid note path: {op.new_path!r}")

    _verify_shas(op)
    if new_target.exists():
        raise ValueError(f"a note already exists at {op.new_path!r}")
    if op.new_path not in {change.path for change in op.changes}:
        # 没有新内容就改名＝把那一篇删了。载荷不全时不许动手。
        raise ValueError(f"the op does not carry the new content for {op.new_path!r}")

    # 先写后删：各档都原子写好之后，才动旧文件（中途失败时旧文件还在）。
    for change in op.changes:
        target = notes_library._safe_note_target(change.path)
        if target is None:
            raise ValueError(f"the change path is not a valid note path: {change.path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, change.text)

    old_target.unlink()

    relatives = [_relative_of(new_target, root), _relative_of(old_target, root)]
    for change in op.changes:
        if change.path == op.new_path:
            continue
        linked = notes_library._safe_note_target(change.path)
        if linked is not None:
            relatives.append(_relative_of(linked, root))

    commit = _commit_notes(
        root, relatives, f"note: rename {op.path} -> {op.new_path}"
    )
    return {
        "kind": RENAME,
        "path": op.path,
        "new_path": op.new_path,
        "trash": "",
        "changed": [change.path for change in op.changes],
        **commit,
    }


def _apply_delete(op: NoteOp) -> dict[str, Any]:
    root = _notes_root().resolve()
    target = notes_library._safe_note_target(op.path)
    if target is None:
        raise ValueError(
            f"note path is outside {NOTES_LOGICAL_ROOT}/ or is not a .md file: {op.path!r}"
        )

    _verify_shas(op)

    # 移进回收站，不 unlink 掉就完事——要可恢复。
    trash_root = _human_root().resolve() / TRASH_DIR
    dest = trash_root / op.path
    if dest.exists():
        dest = _timestamped(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(dest))
    trash_logical = f"{TRASH_DIR}/{dest.relative_to(trash_root).as_posix()}"

    commit = _commit_notes(root, [_relative_of(target, root)], f"note: delete {op.path}")
    return {
        "kind": DELETE,
        "path": op.path,
        "new_path": "",
        "trash": trash_logical,
        "changed": [op.path],
        **commit,
    }


def _commit_notes(root: Path, relatives: Sequence[str], message: str) -> dict[str, Any]:
    """只提交受影响的那几档。

    git 不可用／没变化都**不算失败**（落盘已经完成了），口径与
    :func:`plobi.mind.notes_library._commit_note` 一样。
    """
    blank = {"committed": False, "commit": "", "detail": ""}
    paths = [rel for rel in relatives if rel]
    if not paths:
        return {**blank, "detail": "nothing to commit"}
    try:
        inside = notes_library._git(root, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {**blank, "detail": "notes root is not a git repository"}
        # 回收站那一格（``.trash/``）被那个仓的 .gitignore 挡住了，**不**进
        # 这次的 pathspec——git add 一个被忽略的路径会直接报错。
        added = notes_library._git(root, "add", "-A", "--", *paths)
        if added.returncode != 0:
            return {**blank, "detail": added.stderr.strip() or "git add failed"}
        staged = notes_library._git(root, "diff", "--cached", "--quiet", "--", *paths)
        if staged.returncode == 0:
            return {**blank, "detail": "unchanged"}
        if staged.returncode != 1:
            return {**blank, "detail": staged.stderr.strip() or "git diff failed"}
        done = notes_library._git(root, "commit", "-m", message, "--", *paths)
        if done.returncode != 0:
            return {**blank, "detail": (done.stderr or done.stdout).strip() or "git commit failed"}
        head = notes_library._git(root, "rev-parse", "--short", "HEAD")
        return {"committed": True, "commit": head.stdout.strip(), "detail": ""}
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("notes: commit skipped: %s", exc)
        return {**blank, "detail": str(exc)}
