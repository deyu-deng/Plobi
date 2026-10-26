"""Where Mind lives, and which parts of it we may write.

Three rules from the North Star contract:

- the vault path is resolved from ``MIND_ROOT``; **no drive letters in code**
  (Docs/SPEC-FINAL.md, Mind path rule)
- writes stay inside the prefixes Mind's own verifier tolerates; creating a new
  ``Vault/projects/<x>`` or a skill under ``$PLOBI_HOME/skills`` would require editing Mind's
  AGENTS.md declarations, which no agent may do automatically
- ``Vault/…`` / ``Loom/…`` are **logical** paths (ruling 2026-10-14, decisions.md
  in the brain). The human-facing surface may sit in a sibling repository, so a
  logical path is only meaningful together with the ``Roots`` pair below — never
  concatenate one onto a root by hand.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

# Logical prefixes, stable across both layouts. ``Loom/`` names the brain's own
# subtree even after the flatten, because the string is what ledgers, prompts and
# the desktop speak; only ``real_target`` knows where it physically lives.
VAULT_LOGICAL = "Vault"
MIND_LOGICAL = "Loom"
VAULT_ROOT_ENV = "VAULT_ROOT"

SAFE_PREFIXES: tuple[str, ...] = (
    # 人读面上的产物：一个 Agent 的档案就是它自己那一格（用户 2026-10-25 拍「白名单放开到
    # 全部项目＋研究」——此前只有 ``Vault/projects/Plobi`` 一家，其余 11 个项目与整个
    # ``Vault/research`` 的写在 ``safe_target`` 就抛 ``UnsafeMindPath``，而 writer 按 skip
    # 记账、屏上看不见，这就是档案停在 09-28 的机械原因）。
    # 这一格宽的是**路径**，不是**权限**：写仍过 L1 那道闸（裁 199），日记／画像／删除永不自动。
    "Vault/projects",
    "Vault/research",
    "Vault/courses",
    "Vault/butler",
    "Vault/events",
    "Vault/meta",
    "Vault/notes",
    "Vault/diary",
    "Vault/inbox",
    "Loom/wiki/concepts",
    "Loom/wiki/entities",
    "Loom/wiki/sources",
    "Loom/wiki/comparisons",
    # 记忆系统自己的落点（用户 2026-10-24：「vault 不要进记忆系统」，摘要往 vault 写算越界）：
    # 摘要与打勾回复、以及 L0 写入的镜像，都落在脑里，随定时推上私有远端。
    "Loom/wiki/daily",
    "Loom/wiki/memory",
)

# 记忆系统的活动面 = 上面那张表里属于**脑**的那几格。
#
# ``SAFE_PREFIXES`` 比这一格宽，而且必须宽：课程分身往 ``Vault/notes/<课>/`` 写学科笔记、
# 名册往 ``Vault/research/<项目>`` 登记，那些是**人读面上的产物**，走同一个 MindWriter 的
# 管道路由（裁定 72／126 的两仓拆分＋跨仓 git），不是记忆。
# 用户 2026-10-24/25 两次判「vault 不要进记忆系统」「摘要往 vault 写算越界」，落到代码上
# 就是这一格：记忆的**召回**与**记忆写入**只认 ``Loom/…``，从结构上派生、不另列一张会漂的表。
MEMORY_ZONES: tuple[str, ...] = tuple(
    prefix for prefix in SAFE_PREFIXES if prefix.startswith(f"{MIND_LOGICAL}/")
)


def is_memory_zone(logical: str) -> bool:
    """True when a LOGICAL path belongs to the memory system's own surface.

    The vault deliberately answers ``False`` here even though its zones are
    writable by the plumbing service — see :data:`MEMORY_ZONES` for why the two
    lists are not the same thing.
    """
    text = str(logical or "").replace("\\", "/")
    return any(
        text == zone or text.startswith(f"{zone}/") for zone in MEMORY_ZONES
    )

# AI 写的那两样（当日日程摘要与打勾回复）住**脑里的日页面**，不住原料区、也不住 vault。
#
# 更正留痕（2026-10-25）：下面这段是 10-23/24 写的，其中「进 vault 的 git」「只剩一个落点：
# vault」两句已被后来的裁定盖掉——用户 10-24「vault 不要进记忆系统」、10-25 判往 vault 写
# 「算越界」，摘要与打勾回复因此落在 ``Loom/wiki/daily``（脑仓，随定时推上私有远端）。
# 原文留着是因为它记着当时的判据形状，读的时候以上面这两句为准。
#
# 以前这里是 ``Loom/raw/chat-logs/digested``——那格是旧 Mind 形态（单层 ``Loom/`` ＋
# Mac↔Win 靠 git 传聊天原文）留下的处理区。用户 2026-10-23 10:0x 判「raw 那一类原料在现在
# 的形态里不需要了」（正本在单库 ``state.db``，行级归属，裁定 180），裁定 158 又判「摘要与
# 打勾回复要进 vault 的 git 且入库看得见」。两条合起来只剩一个落点：vault。
#
# 落点在**脑里**（``Loom/wiki/daily``）：用户 2026-10-24「vault 不进记忆系统，往 vault 写算越界」，
# 所以 AI 写的那份回脑、随定时推上私有远端。此前它曾经在 `Vault/projects/Plobi/daily`：2026-10-24 把
# 「项目快照」与「AI 摘要」两条各写一份的路合成了一条——内容本来就是同一个渲染器的同一段话，
# 分成两份的后果是用户要批两遍。这一格不是 ``Vault/diary``／``Vault/notes``：那两格是人写的
# 日记与学科笔记，AI 去那儿放评价性文字正是老注释忌讳的「人读面混进 AI 元评论」。这一格
# 只装**纯事实**（哪天排了什么、哪条勾了、谁勾的），不带评价语气——裁定 158 判据③当场选的
# 就是这一种，写在这里当留痕。vault 故意没有远端（裁定 72／126），所以「原文不上云」与
# 「摘要入库、可被读回」两头同时成立。
AI_WRITE_ROOT = "Loom/wiki/daily"

# Ruling 2026-10-14: the brain loses its ``Loom/`` level (flatten), and the human
# surface leaves for ``VAULT_ROOT``. The move commit flips this one line; until it
# does, every logical ``Loom/…`` still resolves under ``<mind>/Loom``.
#
# Deliberately a declared flag, not "does ``<mind>/Loom`` exist": inferring it from
# the disk answers "flattened" for any empty directory — every test fake vault and
# every fresh clone — and would silently relocate AI writes to the brain root.
# The vault half does not need a flag because ``VAULT_ROOT`` is a positive pointer.
BRAIN_IS_FLATTENED = True


class MindUnavailable(RuntimeError):
    """MIND_ROOT is unset or does not point at a vault."""


class UnsafeMindPath(ValueError):
    """A write was attempted outside the tolerated prefixes."""


def resolve_root(explicit: Path | str | None = None) -> Path | None:
    """Resolve the vault root, or ``None`` when it is not configured.

    Order: explicit argument → ``MIND_ROOT`` → sibling ``mind/`` under the
    repository root → legacy ``Mind/`` beside the repository root. A layout
    that keeps the vault in the data directory is addressed by ``MIND_ROOT``,
    never by guessing where the repo happens to sit.
    """
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_dir() else None

    env = os.environ.get("MIND_ROOT", "").strip()
    if env:
        candidate = Path(env)
        return candidate if candidate.is_dir() else None

    # In-repo layouts: <repo>/mind, then the pre-migration <workspace>/Mind.
    # An unset MIND_ROOT with neither present means "no brain configured here"
    # — callers get ``None``, not a guessed path.
    sibling = Path(__file__).resolve().parents[2] / "mind"
    if (sibling / "AGENTS.md").is_file():
        return sibling

    legacy = Path(__file__).resolve().parents[3] / "Mind"
    if (legacy / "AGENTS.md").is_file():
        return legacy

    return None


def require_root(explicit: Path | str | None = None) -> Path:
    root = resolve_root(explicit)
    if root is None:
        raise MindUnavailable("MIND_ROOT is not set and no Mind vault was found")
    return root


@dataclass(frozen=True)
class Roots:
    """The two roots a logical ``Vault/…`` or ``Loom/…`` path has to be read against.

    ``vault`` is ``None`` while the human surface still lives inside the brain at
    ``<mind>/Vault`` (the pre-2026-10-14 layout). Callers must not branch on it —
    go through :func:`real_target`, which answers the same question once.
    """

    mind: Path
    vault: Optional[Path] = None

    def base_for(self, logical: str) -> Path:
        """The root a logical path is anchored to, without touching the disk."""
        head, _, _ = logical.strip("/").partition("/")
        if head == VAULT_LOGICAL:
            return self.vault if self.vault is not None else self.mind / VAULT_LOGICAL
        return self.mind


def resolve_roots(explicit_mind: Path | str | None = None) -> Optional[Roots]:
    mind = resolve_root(explicit_mind)
    if mind is None:
        return None
    env = os.environ.get(VAULT_ROOT_ENV, "").strip()
    # A pinned pointer that does not exist yet is still the pointer: the surface
    # may be legitimately empty on a fresh setup, and silently falling back to
    # ``<mind>/Vault`` would hand writers two different homes.
    return Roots(mind=mind, vault=Path(env) if env else None)


def real_target(relative: str, root: Union[Path, Roots, None] = None) -> Path:
    """Map a logical Mind path onto wherever it physically lives right now.

    Three layouts are live at once during the move, and all three must keep
    working without a flag:

    1. legacy — ``<mind>/Vault/notes``, ``<mind>/Loom/wiki``
    2. vault split — ``<vault>/notes``, ``<mind>/Loom/wiki``
    3. both done — ``<vault>/notes``, ``<mind>/wiki``

    The vault half is decided by ``VAULT_ROOT`` (an env pointer, never a guess),
    so a write can only ever have one home. The brain half is decided by
    :data:`BRAIN_IS_FLATTENED`, flipped by the move commit itself.
    """
    roots = _roots_or(root)
    cleaned = Path(relative).as_posix().strip("/")
    if not cleaned:
        return roots.mind
    head, _, tail = cleaned.partition("/")
    if head == VAULT_LOGICAL:
        base = roots.base_for(cleaned)
        return base / tail if tail else base
    if head == MIND_LOGICAL and BRAIN_IS_FLATTENED:
        return roots.mind / tail if tail else roots.mind
    return roots.mind / cleaned


def logical_target(target: Path, root: Union[Path, Roots, None] = None) -> Optional[str]:
    """Inverse of :func:`real_target` for a path inside a safe zone.

    Returns ``None`` when ``target`` is outside every prefix in
    :data:`SAFE_PREFIXES`, so this doubles as the write guard used by the memory
    provider — which holds an absolute path and must produce the logical string it
    shows the model.
    """
    roots = _roots_or(root)
    try:
        resolved = target.resolve()
    except OSError:
        return None
    for prefix in SAFE_PREFIXES:
        base = real_target(prefix, roots)
        # No existence check here on purpose: the zones are defined by the path,
        # and a writer creating the first file in ``Loom/raw/chat-logs/exports``
        # must not read as "outside the prefixes" just because the directory is
        # not there yet.
        try:
            inner = resolved.relative_to(base.resolve())
        except ValueError:
            continue
        inner = inner.as_posix()
        return prefix if not inner else f"{prefix}/{inner}"
    return None


def _roots_or(root: Union[Path, Roots, None]) -> Roots:
    if isinstance(root, Roots):
        return root
    if root is not None:
        return Roots(mind=Path(root),
                     vault=Path(os.environ[VAULT_ROOT_ENV]) if os.environ.get(VAULT_ROOT_ENV, "").strip()
                     else None)
    roots = resolve_roots()
    if roots is None:
        raise MindUnavailable("MIND_ROOT is not set and no Mind vault was found")
    return roots


def git_toplevel(path: Path) -> Optional[str]:
    """Top-level of the git repository enclosing ``path``, or ``None``.

    ``None`` means ``path`` sits in no repository at all. Two guards need this and
    they are not the same question: the writer must not commit into a repo it does
    not own, while declaration sync must not run a vault's own gate against — or
    edit files inside — an *enclosing* checkout that merely happens to sit above
    ``MIND_ROOT`` (ruling 72 tail; REQUIREMENTS R-013 edge case ②). A directory in
    no repo at all is neither case, so callers compare against ``path`` rather than
    merely testing for ``None``.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    lines = (completed.stdout or "").strip().splitlines()
    return lines[0] if lines else None


def is_safe_relative(relative: str) -> bool:
    posix = Path(relative).as_posix().lstrip("./")
    if not posix or posix.startswith("..") or Path(posix).is_absolute():
        return False
    return any(posix == prefix or posix.startswith(prefix + "/") for prefix in SAFE_PREFIXES)


def safe_target(
    root: Path, relative: str, *, creating: bool = False
) -> Path:
    """Resolve a logical ``relative`` to the real file, refusing outside the zones.

    The escape check runs against whichever root the zone actually lives under, so
    a split human surface does not read as an escape. ``creating=True`` is the
    writer's guard against resurrecting the old layout: when ``Vault/…`` resolves
    inside the brain but that directory is gone, the surface moved to its own root
    and ``VAULT_ROOT`` was never pinned — creating the directory here would leave
    a second, unbacked-up copy of the user's own notes inside the AI repo.
    """
    if not is_safe_relative(relative):
        raise UnsafeMindPath(f"{relative!r} is outside the writable Mind prefixes")

    roots = _roots_or(root)
    target = real_target(relative, roots)
    base = roots.base_for(Path(relative).as_posix().strip("/"))
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:  # symlink or traversal escape
        raise UnsafeMindPath(f"{relative!r} escapes its root") from exc
    if creating and roots.vault is None and relative.strip("/").startswith(VAULT_LOGICAL):
        if not (roots.mind / VAULT_LOGICAL).is_dir():
            raise UnsafeMindPath(
                f"{relative!r} would recreate {roots.mind / VAULT_LOGICAL} inside the brain repo; "
                f"the human surface has moved — point {VAULT_ROOT_ENV} at it"
            )
    return target
