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

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

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

    # 数据根自带的那一份脑。装机版的家是 ``D:\Data\AppData\Plobi``，它的脑就在
    # ``<家>\mind``——这一档原先没有，所以「家认得、脑找不到」时整条解析落到 ``None``，
    # 笔记页与日记页一起报「MIND_ROOT 未设置或无 Mind surface」（2026-11-08 现场）。
    # 只认**已经存在**的那一份：不猜路径、也不在这里建（建脑是 :func:`ensure_root` 的事，
    # 而它需要写盘，解析函数不该有副作用）。
    home = _data_home()
    if home is not None:
        candidate = home / "mind"
        if candidate.is_dir():
            return candidate

    return None


def _data_home() -> Optional[Path]:
    """数据根（``PLOBI_HOME``）——脑在它下面的 ``mind/`` 那一格。

    唯一权威是 :func:`plobi_constants.get_plobi_home`（装机版认安装记录
    ``HKCU\\Software\\Plobi\\DataDir``、开发态认 ``PLOBI_DEV_HOME``），这里只是借用，
    不另立一套判定——两处判定一旦分叉，就会出现「进程写一个家、工具读另一个家」。
    延迟 import：``plobi_constants`` 在 import 期不碰本模块，但反过来会成环。
    """
    try:
        from plobi_constants import get_plobi_home

        value = str(get_plobi_home() or "").strip()
    except Exception:  # pragma: no cover - 家都算不出来时按「没配」处理
        return None
    return Path(value) if value else None


def require_root(explicit: Path | str | None = None) -> Path:
    root = resolve_root(explicit)
    if root is None:
        raise MindUnavailable("MIND_ROOT is not set and no Mind surface was found")
    return root


# --- 首次启动自建最小脑 ---------------------------------------------------------
#
# 2026-11-08 现场：装机版 0.3.0 的家是 ``D:\Data\AppData\Plobi``，那个家下面从来没有
# ``mind``。``resolve_root`` 答 ``None`` ⇒ ``MindProvider`` 拿到一个临时目录占位 ⇒ 笔记页
# 与日记页同时报「MIND_ROOT 未设置或无 Mind surface」。装完就用的人面对两台空白页，
# 而这不是配置错，是**新家没有脑**，却没有任何一处代码负责把它建出来。
#
# 所以动手的只有两件事：解析多认一档「数据根自带的脑」（见 :func:`resolve_root`，只认
# 已存在的），以及 :func:`ensure_root` 把那一档建出来——幂等、只补缺、绝不覆盖。

MINIMAL_AGENTS_MD = """# Mind — 本机自己的脑

这份脑由 Plobi 首次启动时自动建出来（`plobi.mind.paths.ensure_root`）。初始为空：没有画像、
没有档案、没有 wiki 页面，这些都是用起来之后才长出来的东西。**它不声明任何东西**——
§1 的项目清单声明行留空，等真的有了项目再补。

## 目录

| 位置 | 装什么 | 谁写 |
|---|---|---|
| `wiki/daily` | 当日日程摘要与打勾回复（纯事实，不带评价语气） | Plobi |
| `wiki/memory` | Plobi 记忆工具镜像进来的条目 | Plobi |
| `wiki/concepts` `wiki/entities` `wiki/sources` `wiki/comparisons` | 知识页（受词表闸约束） | Plobi |
| `persona` | 用户画像详版 `profile.md` | Plobi |
| `dossiers` | 各 Agent 的档案 | Plobi |
| `Surface/notes` | 学科笔记 | 人 |
| `Surface/diary` | 日记 `YYYY-MM-DD-Weekday.md` | 人 |

人读面若被 `SURFACE_ROOT` 指到别处，`Surface/…` 就落在那边，不在这里。

## 规矩

- 写者边界看 `Code/plobi/mind/paths.py` 的 `SAFE_PREFIXES`，那张表是唯一真源。
- 人写的 `Surface/notes`、`Surface/diary` 里不放 AI 元注释。
"""


def _mind_relative(prefix: str) -> str:
    """逻辑前缀 → 相对脑根的真实前缀（摊平之后 ``Mind/wiki/x`` 就是 ``wiki/x``）。"""
    head, _, rest = prefix.partition("/")
    if head != MIND_LOGICAL:
        return prefix
    return rest if BRAIN_IS_FLATTENED else prefix


def brain_skeleton() -> tuple[str, ...]:
    """最小脑要有的目录（相对脑根）。

    从 :data:`SAFE_PREFIXES` 派生，不另立一张会漂的表——写者允许写哪里，就先备好哪里。
    """
    out: list[str] = []
    for prefix in SAFE_PREFIXES:
        rel = _mind_relative(prefix)
        if rel.startswith(f"{SURFACE_LOGICAL}/"):
            continue  # 人读面不归脑根，见 surface_skeleton()
        if rel and rel not in out:
            out.append(rel)
    return tuple(out)


def surface_skeleton() -> tuple[str, ...]:
    """人读面要有的目录（相对人读面根）。"""
    out: list[str] = []
    for prefix in SAFE_PREFIXES:
        head, _, rest = prefix.partition("/")
        if head != SURFACE_LOGICAL or not rest:
            continue
        # 只备到第一层：``Surface/dossiers/projects`` 只要 ``dossiers`` 存在，项目目录按需长。
        first = rest.partition("/")[0]
        if first and first not in out:
            out.append(first)
    return tuple(out)


def _intended_root(explicit: Path | str | None = None) -> Optional[Path]:
    """脑**应该**落在哪——不管它现在存不存在。

    :func:`resolve_root` 判的是「这个脑够不够格被读」，这里答的是「该往哪儿建」。
    两者共用同一架梯子的顺序（显式 → ``MIND_ROOT`` → 仓内 ``mind/`` → 旧 ``Mind/`` →
    数据根 ``<PLOBI_HOME>/mind``）；改梯子时两边一起看。
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("MIND_ROOT", "").strip()
    if env:
        return Path(env)
    for base in (
        Path(__file__).resolve().parents[2] / "mind",
        Path(__file__).resolve().parents[3] / "Mind",
    ):
        if (base / "AGENTS.md").is_file():
            return base
    home = _data_home()
    return (home / "mind") if home is not None else None


def ensure_root(explicit: Path | str | None = None) -> Optional[Path]:
    """把脑建出来并返回它，已经存在就只补缺。幂等，绝不覆盖既有文件。

    建的是**结构**，不是内容：目录 ＋ 一份最小 ``AGENTS.md``（只要还没有那份文件）。
    ``AGENTS.md`` 是脑自己的契约文件，人写的，所以这里只在它缺席时补一份最朴素的，
    已经有任何一版就一个字节都不动。

    返回建完之后的脑根；家都算不出来（既无指针也无数据根）时返回 ``None``。
    """
    target = resolve_root(explicit) or _intended_root(explicit)
    if target is None:
        return None

    created: list[str] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        for rel in brain_skeleton():
            path = target / rel
            if not path.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                created.append(rel)
        agents = target / "AGENTS.md"
        if not agents.is_file():
            agents.write_text(MINIMAL_AGENTS_MD, encoding="utf-8")
            created.append("AGENTS.md")
        # 人读面已经存在才补它的子目录——指针指着一个还没建出来的库时不动手，
        # 免得替用户在他自己的 Obsidian 库旁边造格子。
        surface = _surface_root(target)
        if surface is not None and surface.is_dir():
            for rel in surface_skeleton():
                path = surface / rel
                if not path.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    created.append(f"<surface>/{rel}")
    except OSError as exc:
        logger.warning("[mind] could not initialise brain at %s: %s", target, exc)
        return resolve_root(explicit)

    if created:
        logger.info(
            "[mind] initialised brain at %s (created: %s)", target, ", ".join(created)
        )
    return target


@dataclass(frozen=True)
class Roots:
    """The two roots a logical ``Surface/…`` or ``Mind/…`` path has to be read against.

    ``surface`` is the **data home** — the human surface lives in it as
    ``notes/``／``diary/``／``dossiers/``, the brain in ``mind/`` (用户 11-08 16:4x
    拍的口径：一个数据目录，不需要环境变量也能跑）——除非 ``SURFACE_ROOT`` 那根
    指针把它指到别处。它是 ``None`` 只在**数据家本身都算不出来**的时候；调用方
    不许拿它分叉——走 :func:`real_target`，那一只口答同一件事。
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
    """人读面**钉**在哪儿：先看继承来的 env，再看注册表那份钉的。

    env 与注册表不一致时，**只有「env 指的那个目录已经不在了」才改读注册表**：

    - env 指的目录还在 ⇒ 用 env（env 是当下的选择，用户可能故意临时指到别处）；
    - env 指的目录不在、注册表那份在 ⇒ env 是**继承来的陈旧值**，改读注册表
      （这台机器上长驻进程的常态，见 :func:`_pinned_surface_root`）；
    - env 指的目录不在、注册表也没钉或也一样 ⇒ 仍用 env。这条保住「指针先钉、
      表面后建」那条设计（新装时表面还没建出来，指针照样算数），也保住「不静默换家」。

    返回 ``None`` = **压根没钉**（落哪儿由 :func:`_surface_root` 答）。
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


def _surface_root(mind: Path | str | None = None) -> Optional[Path]:
    """人读面**落在哪儿**：钉住的地方 ⇒ 钉住的地方；没钉 ⇒ 脑旁边的老布局（若还在）
    ⇒ **数据家本体**。

    2026-11-08（用户 11-08 16:4x 拍的口径）：「**这个是我决定的。数据目录不是就一个就行了吗？
    而且不需要环境变量也可以运行呀。mind和日记还有笔记和档案都是用户的不过是用户指定数据目录下的
    不同文件夹而已。**」⇒ 没钉指针时，人读面就是**数据家本体**（``<家>/notes``／``<家>/diary``／
    ``<家>/dossiers``），脑在 ``<家>/mind``——**一个家，不靠环境变量**。

    为什么还留着「老布局」那一档：有的机器（这台**装机态**那一份）人读面**已经**在
    ``<脑>/Surface`` 里（数据家的 ``notes``／``diary`` 是指着它的 junction）——那就
    **不静默换家**：它在那儿就还认它。换家＝把用户自己的笔记挪到别处去，那是另一件事。

    开发态那一份的脑仓里**没有** ``Surface``（现核：``D:\\Data\\AppData\\Plobi-Dev\\mind``
    下面是 ``wiki``／``persona``／``raw``／…）⇒ 它落到数据家本体，与用户说的那个形状一致。
    """
    pinned = _surface_pointer()
    if pinned is not None:
        return pinned

    brain = Path(mind) if mind is not None else resolve_root()
    if brain is not None:
        legacy = brain / SURFACE_LOGICAL
        try:
            if legacy.is_dir():
                return legacy
        except OSError:
            pass
    return _data_home()


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
    return Roots(mind=mind, surface=_surface_root(mind))


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
        return Roots(mind=Path(root), surface=_surface_root(Path(root)))
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
