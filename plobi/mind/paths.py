"""Where Mind lives, and which parts of it we may write.

Three rules from the North Star contract:

- the surface path is resolved from ``MIND_ROOT``; **no drive letters in code**
  (Docs/SPEC-FINAL.md, Mind path rule)
- writes stay inside the prefixes Mind's own verifier tolerates; creating a new
  ``Surface/dossiers/projects/<x>`` or a skill under ``$PLOBI_HOME/skills`` would require editing Mind's
  AGENTS.md declarations, which no agent may do automatically
- ``Surface/…`` / ``Mind/…`` are **logical** paths (ruling 2026-10-14, decisions.md
  in the brain). The human-facing surface may sit in a sibling repository, so a
  logical path is only meaningful together with the ``Roots`` pair below — never
  concatenate one onto a root by hand.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

# Logical prefixes, stable across both layouts. ``Mind/`` names the brain's own
# subtree even after the flatten, because the string is what ledgers, prompts and
# the desktop speak; only ``real_target`` knows where it physically lives.
SURFACE_LOGICAL = "Surface"
MIND_LOGICAL = "Mind"
SURFACE_ROOT_ENV = "SURFACE_ROOT"

SAFE_PREFIXES: tuple[str, ...] = (
    # 人读面上的产物：一个 Agent 的档案就是它自己那一格（用户 2026-10-25 拍「白名单放开到
    # 全部项目＋研究」——此前只有 ``Surface/dossiers/projects/Plobi`` 一家，其余 11 个项目与整个
    # ``Surface/dossiers/research`` 的写在 ``safe_target`` 就抛 ``UnsafeMindPath``，而 writer 按 skip
    # 记账、屏上看不见，这就是档案停在 09-28 的机械原因）。
    # 这一格宽的是**路径**，不是**权限**：写仍过 L1 那道闸（裁 199），日记／画像／删除永不自动。
    "Surface/dossiers/projects",
    "Surface/dossiers/research",
    "Surface/dossiers/courses",
    "Surface/dossiers/butler",
    "Surface/dossiers/events",
    "Surface/meta",
    "Surface/notes",
    "Surface/diary",
    "Surface/inbox",
    "Mind/wiki/concepts",
    "Mind/wiki/entities",
    "Mind/wiki/sources",
    "Mind/wiki/comparisons",
    # 记忆系统自己的落点（用户 2026-10-24：「surface 不要进记忆系统」，摘要往 surface 写算越界）：
    # 摘要与打勾回复、以及 L0 写入的镜像，都落在脑里，随定时推上私有远端。
    "Mind/wiki/daily",
    "Mind/wiki/memory",
)

# 记忆系统的活动面 = 上面那张表里属于**脑**的那几格。
#
# ``SAFE_PREFIXES`` 比这一格宽，而且必须宽：课程分身往 ``Surface/notes/<课>/`` 写学科笔记、
# 名册往 ``Surface/dossiers/research/<项目>`` 登记，那些是**人读面上的产物**，走同一个 MindWriter 的
# 管道路由（裁定 72／126 的两仓拆分＋跨仓 git），不是记忆。
# 用户 2026-10-24/25 两次判「surface 不要进记忆系统」「摘要往 surface 写算越界」，落到代码上
# 就是这一格：记忆的**召回**与**记忆写入**只认 ``Mind/…``，从结构上派生、不另列一张会漂的表。
MEMORY_ZONES: tuple[str, ...] = tuple(
    prefix for prefix in SAFE_PREFIXES if prefix.startswith(f"{MIND_LOGICAL}/")
)


def is_memory_zone(logical: str) -> bool:
    """True when a LOGICAL path belongs to the memory system's own surface.

    The surface deliberately answers ``False`` here even though its zones are
    writable by the plumbing service — see :data:`MEMORY_ZONES` for why the two
    lists are not the same thing.
    """
    text = str(logical or "").replace("\\", "/")
    return any(
        text == zone or text.startswith(f"{zone}/") for zone in MEMORY_ZONES
    )

# AI 写的那两样（当日日程摘要与打勾回复）住**脑里的日页面**，不住原料区、也不住 surface。
#
# 更正留痕（2026-10-25）：下面这段是 10-23/24 写的，其中「进 surface 的 git」「只剩一个落点：
# surface」两句已被后来的裁定盖掉——用户 10-24「surface 不要进记忆系统」、10-25 判往 surface 写
# 「算越界」，摘要与打勾回复因此落在 ``Mind/wiki/daily``（脑仓，随定时推上私有远端）。
# 原文留着是因为它记着当时的判据形状，读的时候以上面这两句为准。
#
# 以前这里是 ``Mind/raw/chat-logs/digested``——那格是旧 Mind 形态（单层 ``Mind/`` ＋
# Mac↔Win 靠 git 传聊天原文）留下的处理区。用户 2026-10-23 10:0x 判「raw 那一类原料在现在
# 的形态里不需要了」（正本在单库 ``state.db``，行级归属，裁定 180），裁定 158 又判「摘要与
# 打勾回复要进 surface 的 git 且入库看得见」。两条合起来只剩一个落点：surface。
#
# 落点在**脑里**（``Mind/wiki/daily``）：用户 2026-10-24「surface 不进记忆系统，往 surface 写算越界」，
# 所以 AI 写的那份回脑、随定时推上私有远端。此前它曾经在 `Surface/dossiers/projects/Plobi/daily`：2026-10-24 把
# 「项目快照」与「AI 摘要」两条各写一份的路合成了一条——内容本来就是同一个渲染器的同一段话，
# 分成两份的后果是用户要批两遍。这一格不是 ``Surface/diary``／``Surface/notes``：那两格是人写的
# 日记与学科笔记，AI 去那儿放评价性文字正是老注释忌讳的「人读面混进 AI 元评论」。这一格
# 只装**纯事实**（哪天排了什么、哪条勾了、谁勾的），不带评价语气——裁定 158 判据③当场选的
# 就是这一种，写在这里当留痕。surface 故意没有远端（裁定 72／126），所以「原文不上云」与
# 「摘要入库、可被读回」两头同时成立。
AI_WRITE_ROOT = "Mind/wiki/daily"

# Ruling 2026-10-14: the brain loses its ``Mind/`` level (flatten), and the human
# surface leaves for ``SURFACE_ROOT``. The move commit flips this one line; until it
# does, every logical ``Mind/…`` still resolves under ``<mind>/Mind``.
#
# Deliberately a declared flag, not "does ``<mind>/Mind`` exist": inferring it from
# the disk answers "flattened" for any empty directory — every test fake surface and
# every fresh clone — and would silently relocate AI writes to the brain root.
# The surface half does not need a flag because ``SURFACE_ROOT`` is a positive pointer.
BRAIN_IS_FLATTENED = True


class MindUnavailable(RuntimeError):
    """MIND_ROOT is unset or does not point at a surface."""


class UnsafeMindPath(ValueError):
    """A write was attempted outside the tolerated prefixes."""


def resolve_root(explicit: Path | str | None = None) -> Path | None:
    """Resolve the surface root, or ``None`` when it is not configured.

    Order: explicit argument → ``MIND_ROOT`` → sibling ``mind/`` under the
    repository root → legacy ``Mind/`` beside the repository root. A layout
    that keeps the surface in the data directory is addressed by ``MIND_ROOT``,
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
        raise MindUnavailable("MIND_ROOT is not set and no Mind surface was found")
    return root


@dataclass(frozen=True)
class Roots:
    """The two roots a logical ``Surface/…`` or ``Mind/…`` path has to be read against.

    ``surface`` is ``None`` while the human surface still lives inside the brain at
    ``<mind>/Surface`` (the pre-2026-10-14 layout). Callers must not branch on it —
    go through :func:`real_target`, which answers the same question once.
    """

    mind: Path
    surface: Optional[Path] = None

    def base_for(self, logical: str) -> Path:
        """The root a logical path is anchored to, without touching the disk."""
        head, _, _ = logical.strip("/").partition("/")
        if head == SURFACE_LOGICAL:
            return self.surface if self.surface is not None else self.mind / SURFACE_LOGICAL
        return self.mind


def _pinned_surface_root() -> Optional[str]:
    """Windows: the ``SURFACE_ROOT`` that was pinned into the user env (HKCU\\Environment).

    ``setx``／``[Environment]::SetEnvironmentVariable`` 只对**之后启动**的进程生效。
    长驻进程（桌面 app、CodeBuddy、计划任务、旧 shell）在改钉之前就起来了 ⇒ 它的
    env 里还是**旧值**。

    2026-11-03 实测撞上这一型：人读面从 ``<PLOBI_HOME>/surface`` 搬到 ``<PLOBI_HOME>``
    本体之后，注册表里是新的，长驻的桌面 app 却还按继承来的旧值去找
    ``<PLOBI_HOME>/surface/notes`` ⇒ 日记页与笔记页同时报「读不到 surface」。
    同一个家在两种进程里解析到两个地方——这正是
    :func:`plobi_constants.get_plobi_home` 早就为 ``PLOBI_HOME`` 修过的那一类
    （那边读注册表兜底，见 ``_pinned_home_from_registry``）。这里同形同治。

    非 Windows / 读不到 ⇒ ``None``（行为与改动前逐位相同）。
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, SURFACE_ROOT_ENV)
    except (OSError, FileNotFoundError, ImportError):
        return None
    text = str(value or "").strip()
    return text or None


def _surface_pointer() -> Optional[Path]:
    """人读面现在物理上住在哪：先看继承来的 env，再看注册表那份钉的。

    env 与注册表不一致时，**只有「env 指的那个目录已经不在了」才改读注册表**：

    - env 指的目录还在 ⇒ 用 env（env 是当下的选择，用户可能故意临时指到别处）；
    - env 指的目录不在、注册表那份在 ⇒ env 是**继承来的陈旧值**，改读注册表
      （这台机器上长驻进程的常态，见 :func:`_pinned_surface_root`）；
    - env 指的目录不在、注册表也没钉或也一样 ⇒ 仍用 env。这条保住「指针先钉、
      表面后建」那条设计（新装时表面还没建出来，指针照样算数），也保住「不静默换家」。

    返回 ``None`` = 压根没钉 ⇒ 人读面按老布局落在 ``<MIND_ROOT>/Surface``。
    """
    env = os.environ.get(SURFACE_ROOT_ENV, "").strip()
    pinned = _pinned_surface_root()

    if not env:
        # 没钉过：注册表那份是唯一的钉（长驻进程在改钉之前起来 ⇒ env 里压根没有这一项）。
        return Path(pinned) if pinned else None

    candidate = Path(env)
    if pinned and pinned != env:
        try:
            stale = not candidate.exists()
        except OSError:
            stale = False
        if stale and Path(pinned).exists():
            return Path(pinned)
    return candidate


def resolve_roots(explicit_mind: Path | str | None = None) -> Optional[Roots]:
    mind = resolve_root(explicit_mind)
    if mind is None:
        return None
    # A pinned pointer that does not exist yet is still the pointer: the surface
    # may be legitimately empty on a fresh setup, and silently falling back to
    # ``<mind>/Surface`` would hand writers two different homes.  (The one
    # exception is a pointer whose directory is *gone* while the registry pin is
    # alive — that is a stale inherited value, not a fresh setup; see
    # :func:`_surface_pointer`.)
    return Roots(mind=mind, surface=_surface_pointer())


def real_target(relative: str, root: Union[Path, Roots, None] = None) -> Path:
    """Map a logical Mind path onto wherever it physically lives right now.

    Three layouts are live at once during the move, and all three must keep
    working without a flag:

    1. legacy — ``<mind>/Surface/notes``, ``<mind>/Mind/wiki``
    2. surface split — ``<surface>/notes``, ``<mind>/Mind/wiki``
    3. both done — ``<surface>/notes``, ``<mind>/wiki``

    The surface half is decided by ``SURFACE_ROOT`` (an env pointer, never a guess),
    so a write can only ever have one home. The brain half is decided by
    :data:`BRAIN_IS_FLATTENED`, flipped by the move commit itself.
    """
    roots = _roots_or(root)
    cleaned = Path(relative).as_posix().strip("/")
    if not cleaned:
        return roots.mind
    head, _, tail = cleaned.partition("/")
    if head == SURFACE_LOGICAL:
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
        # and a writer creating the first file in ``Mind/raw/chat-logs/exports``
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
        # 同一份解析，别在这里另读一遍 env（那条 raw 读法不认识注册表那份钉，
        # 也不认识「继承来的旧值」——两处口径一散，就是「同一个家两种进程两个地方」）。
        return Roots(mind=Path(root), surface=_surface_pointer())
    roots = resolve_roots()
    if roots is None:
        raise MindUnavailable("MIND_ROOT is not set and no Mind surface was found")
    return roots


def git_toplevel(path: Path) -> Optional[str]:
    """Top-level of the git repository enclosing ``path``, or ``None``.

    ``None`` means ``path`` sits in no repository at all. Two guards need this and
    they are not the same question: the writer must not commit into a repo it does
    not own, while declaration sync must not run a surface's own gate against — or
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
    writer's guard against resurrecting the old layout: when ``Surface/…`` resolves
    inside the brain but that directory is gone, the surface moved to its own root
    and ``SURFACE_ROOT`` was never pinned — creating the directory here would leave
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
    if creating and roots.surface is None and relative.strip("/").startswith(SURFACE_LOGICAL):
        if not (roots.mind / SURFACE_LOGICAL).is_dir():
            raise UnsafeMindPath(
                f"{relative!r} would recreate {roots.mind / SURFACE_LOGICAL} inside the brain repo; "
                f"the human surface has moved — point {SURFACE_ROOT_ENV} at it"
            )
    return target
