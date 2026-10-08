"""建完一棵 Mind 项目树之后，脑仓自己那两处声明必须跟着走。

:mod:`plobi.mind.project_tree` 只负责「建目录 + 三件套」那一半。光有磁盘那一半在脑仓里
是**不合格**的：Mind 自己的门禁 ``Loom/scripts/verifier.py`` 的 ``check_projects()`` 把
「``AGENTS.md`` §1 声明的 projects 列表 ≠ ``Vault/projects`` 磁盘」判成 BLOCKER（退出码 1），
而 ``mind/.githooks/pre-commit`` 已经装上（``core.hooksPath=.githooks``）——于是只建目录不同步
声明，用户在脑仓的下一次提交会被他自己的 verifier 拦下，拦在一个**我们**刚造出来的洞上。

这里补的就是那一格，两件东西按各自的真源来：

* ``Vault/projects/INDEX.md`` —— **不自己写生成器**，调脑仓自己的
  ``Loom/scripts/verifier.py --fix-projects``（它就是「依据 ``plan.md`` frontmatter 重生成
  INDEX.md」那个工具，见其 ``check_or_fix_project_registry``）。脚本按**我们所用的那个 Mind 根**
  定位（``<mind_root>/Loom/scripts/verifier.py``，与 :mod:`plobi.mind.writer` 里
  ``VERIFIER_RELATIVE`` 同一份相对路径），并以该根为 cwd 起子进程：verifier 的根是
  ``git rev-parse --show-toplevel``，真脑仓（``MIND_ROOT`` 指到的那个）自带 ``.git``，那条命令给出的就是这个
  根；根不是仓时它退化到「脚本自身往上两级」，同样是这个根。两条路都指回我们传进去的那个根，
  所以既不用改脑仓的脚本，也不用给它加一个 Mind 专用参数。
* ``AGENTS.md`` §1 那一行 —— 生成器不碰它（它只读），所以由我们改。形状照脑仓里真实那行：
  ``└── projects/           ← 13 个项目（含 Mind 系统自身）：Aura / Framelet / …``；计数与列表
  两处都按**盘上当下的目录名**重排。读它的正则与 ``check_projects()`` 同形（见
  :data:`_DECLARATION_RE` 那段注释）——两边各写一套正则，等于我们更新的不是门禁读的那一行。

两条规则：

1. **顺序**：目录三件套（:func:`plobi.mind.project_tree.ensure_project_tree`）→ §1 →
   ``--fix-projects`` → 最后跑一次不带参数的 verifier 断言退出码 0。断言不过就整体回滚。
2. **失败即创建失败**：任一步没成，本模块先把自己改过的两处逐字节还原再抛
   :class:`DeclarationSyncError`；调用方（``plobi/console/router.py``）据此收回项目树，名册那一行
   压根不写——不留「磁盘有目录、声明里没有」的中间态。

不碰的第三样：``plan.md`` 的 frontmatter 由项目树那一半负责，这里只读盘上的目录名。
命名按脑仓 ``AGENTS.md`` §5：项目名大小写按项目名本身、多词以 ``-`` 连接，不强制 kebab-case——
落到声明里的是**磁盘上那个名字**（:class:`plobi.mind.project_tree.ProjectTreeWrite` 已经按盘上真名
取过大小写），这里不再二次归一，否则同一个项目又有第二个写法。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .lock import MindLockTimeout, mind_write_lock
from .paths import git_toplevel, resolve_root

logger = logging.getLogger(__name__)

AGENTS_RELATIVE = "AGENTS.md"
# 与 plobi/mind/writer.py:23 同一个相对路径：脑仓的门禁脚本只有一个位置。
VERIFIER_RELATIVE = "Loom/scripts/verifier.py"
INDEX_RELATIVE = "Vault/projects/INDEX.md"
PROJECTS_RELATIVE = "Vault/projects"
# 与 MindWriter._lock_path（plobi/mind/writer.py:63）同一个锁文件——串行的是同一把，
# 别给同一个仓配两把锁。
LOCK_RELATIVE = ".plobi-mind.lock"

_VERIFIER_TIMEOUT_SECONDS = 120.0
_LOCK_TIMEOUT_SECONDS = 30.0
_DETAIL_TAIL_CHARS = 600
_LIST_SEPARATOR = " / "
# §1 那行在树图里长这样：``└── projects/  ← 13 个项目（…）：Aura / …``。树形标记用来认行，
# 不去改正文里某句「以下 3 个项目：」的散文（散文行门禁读不到，改了就是自己造第二个声明）。
_TREE_LINE_MARKER = "projects/"

# Mind 的 verifier ``check_projects()`` 读声明行用的是 r"(\\d+)\\s*个项目[^\\n]*?：([^\\n]+)"：
# 数字 + 「个项目」+ 到**同一行**第一个全角冒号 + 冒号后到行尾。这里只把计数单独圈出来好替换，
# 列表段多容许一种情况：冒号后**空着**（``0 个项目（…）：`` 那种还没立过项的行）。多容许的
# 这一种只在带 ``projects/`` 树形标记的行上接受（见 :func:`_find_declaration`），补上第一个名字
# 之后门禁才读得到这行；除此之外匹配范围与它一致。
_DECLARATION_RE = re.compile(r"(\d+)(\s*个项目[^\n]*?：)([^\n]*)")


class DeclarationSyncError(RuntimeError):
    """两处声明同步不成——这次创建按失败处理（调用方回滚项目树，名册那行不写）。"""


@dataclass(frozen=True)
class DeclarationSync:
    """一次声明同步的落地结果，带着「怎么撤销自己」的账。

    ``restored`` 记的是**改动前的字节**（``None`` = 那回改动前根本没这个文件）：回滚就是逐字节
    还原，不是「再改一版回去」——AGENTS.md 是人写的，我们不替他重新组织。
    """

    dir_name: str
    updated_agents_line: bool = False
    regenerated_index: bool = False
    skipped: str = ""
    root: Path = field(default=Path(), repr=False)
    restored: tuple[tuple[str, bytes | None], ...] = ()

    def rollback(self) -> None:
        """把这次同步改过的两处还原成改动前的样子（尽力而为，失败只记日志）。"""
        _restore_declarations(self.root, dict(self.restored))


def _find_declaration(text: str) -> re.Match | None:
    """挑出「那一条」声明行：先认 §1 树图里的 ``projects/`` 行，其次门禁自己也读到的那条。

    顺序要说清：脑仓真实文件里这两条是同一行（行 88 既带 ``projects/`` 也是第一个匹配）。分开
    两条规则是为了两种偏角——① 冒号后空着的树行（还没立过项），verifier 的正则读不到（它要求
    至少一个字符），我们读得到，于是第一次立项就能把它填起来；② 正文里的散文行「以下 3 个
    项目：」——它不带 ``projects/``，verifier 也读不到（冒号后是行尾），两边一致地跳过它。
    """
    tree_match = None
    plain_match = None
    for match in _DECLARATION_RE.finditer(text):
        # 匹配是从**数字**起的，行首的 ``└── projects/`` 在匹配之外——要认这一行得先退回行首。
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.start())
        line = text[line_start: len(text) if line_end == -1 else line_end]
        if _TREE_LINE_MARKER in line:
            tree_match = match
            break
        if match.group(3).strip() and plain_match is None:
            plain_match = match
    return tree_match or plain_match


def declared_projects(agents_text: str) -> list[str] | None:
    """``AGENTS.md`` §1 那行声明的项目目录名；没有声明行返回 ``None``。

    切法与 verifier 一致：冒号后按 ``/`` 切、丢掉空串。
    """
    match = _find_declaration(agents_text)
    if match is None:
        return None
    return [item.strip() for item in match.group(3).split("/") if item.strip()]


def render_declaration(agents_text: str, names: list[str]) -> tuple[str, bool]:
    """把 §1 那行的计数与列表按 ``names``（盘上真名，已排序）重写；返回 (新文本, 是否变了)。

    没有声明行 → 原样返回、``False``：这份 AGENTS.md 此刻不声明项目清单，verifier 对这种库也只
    报 WARN（``AGENTS §1 未找到项目列表声明``），我们不去猜一个形状、也不另起一处声明。
    """
    match = _find_declaration(agents_text)
    if match is None:
        return agents_text, False
    listed = match.group(3)
    if not listed.strip():
        separator = _LIST_SEPARATOR
    else:
        separator = _LIST_SEPARATOR if _LIST_SEPARATOR in listed else "/"
    rendered_list = separator.join(names)
    if rendered_list == listed and match.group(1) == str(len(names)):
        return agents_text, False
    rebuilt = (
        agents_text[: match.start()]
        + str(len(names))
        + match.group(2)
        + rendered_list
        + agents_text[match.end():]
    )
    return rebuilt, True


def project_dir_names(root: Path) -> list[str]:
    """``<root>/Vault/projects`` 下的目录名（排序）——声明与 INDEX 都对齐到它。"""
    base = root / PROJECTS_RELATIVE
    if not base.is_dir():
        raise DeclarationSyncError(
            f"{root} 里没有 {PROJECTS_RELATIVE}/，项目清单对不了盘"
        )
    try:
        return sorted(child.name for child in base.iterdir() if child.is_dir())
    except OSError as exc:
        raise DeclarationSyncError(f"{PROJECTS_RELATIVE}/ 列不出来：{exc}") from exc


def _run_verifier(root: Path, *args: str) -> subprocess.CompletedProcess:
    """以 ``root`` 为 cwd 跑那个库**自己的** verifier 脚本。

    ``PYTHONIOENCODING`` + ``encoding="utf-8"`` 是成对的：verifier 打印中文，本机 codepage 不该
    决定我们读回什么字节（解码炸在异常处理之外就等于把「声明同步失败」变成一句裸 500）。
    """
    script = root / VERIFIER_RELATIVE
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_VERIFIER_TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise DeclarationSyncError(
            f"{VERIFIER_RELATIVE} 跑不起来（{type(exc).__name__}: {exc}）——"
            f"Mind 的门禁没法执行，两处声明也就没法确认同步"
        ) from exc


def _output_tail(completed: subprocess.CompletedProcess) -> str:
    text = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    return text[-_DETAIL_TAIL_CHARS:] or f"退出码 {completed.returncode}，无输出"


def _snapshot(path: Path, relative: str, snapshot: dict[str, bytes | None]) -> None:
    if relative in snapshot:
        return
    try:
        snapshot[relative] = path.read_bytes() if path.is_file() else None
    except OSError as exc:
        raise DeclarationSyncError(f"{relative} 读不出改动前的样子，不敢改：{exc}") from exc


def _update_agents_line(
    *,
    agents_path: Path,
    names: list[str],
    snapshot: dict[str, bytes | None],
) -> bool:
    """第 ① 步：§1 那一行按盘上清单重写；返回是否真改了。"""
    if not agents_path.is_file():
        # AGENTS.md 不在：verifier 自己会把它判成 BLOCKER（第 ③ 步拦下），这里不猜形状。
        return False
    _snapshot(agents_path, AGENTS_RELATIVE, snapshot)
    try:
        text = agents_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DeclarationSyncError(f"{AGENTS_RELATIVE} 读不成：{exc}") from exc
    rebuilt, changed = render_declaration(text, names)
    if not changed:
        return False
    try:
        agents_path.write_text(rebuilt, encoding="utf-8")
    except OSError as exc:
        raise DeclarationSyncError(f"{AGENTS_RELATIVE} §1 那一行写不下去：{exc}") from exc
    return True


def _regenerate_index(
    *,
    root: Path,
    index_path: Path,
    snapshot: dict[str, bytes | None],
) -> bool:
    """第 ② 步：调脑仓自己的 ``--fix-projects`` 重生成 ``Vault/projects/INDEX.md``。"""
    _snapshot(index_path, INDEX_RELATIVE, snapshot)
    completed = _run_verifier(root, "--fix-projects")
    if completed.returncode != 0:
        raise DeclarationSyncError(
            f"{VERIFIER_RELATIVE} --fix-projects 没跑成（退出码 {completed.returncode}）："
            f"{_output_tail(completed)}"
        )
    return True


def _assert_gate(root: Path) -> None:
    """第 ③ 步：跑一次脑仓自己的门禁，退出码必须 0（pre-commit 跑的那次，同一个判据）。"""
    completed = _run_verifier(root)
    if completed.returncode != 0:
        raise DeclarationSyncError(
            f"Mind 的门禁不放行这次立项（{VERIFIER_RELATIVE} 退出码 "
            f"{completed.returncode}）：{_output_tail(completed)}"
        )


def _restore_declarations(root: Path, snapshot: Mapping[str, bytes | None]) -> None:
    """逐字节还原快照里的文件；快照里是 ``None`` 的（原本不存在）就删掉。"""
    for relative, payload in snapshot.items():
        path = root / relative
        try:
            if payload is None:
                if path.is_file():
                    path.unlink()
            else:
                path.write_bytes(payload)
        except OSError as exc:
            logger.warning(
                "mind: 声明回滚没成 %s: %s——磁盘留的是改动后的那版，"
                "去脑仓里手工对齐（git diff AGENTS.md / Vault/projects/INDEX.md）",
                relative, exc,
            )


def sync_project_declarations(
    *, dir_name: str, mind_root: Path | str | None = None
) -> DeclarationSync:
    """把 ``Vault/projects/<dir_name>`` 同步进脑仓的两处声明，成功才返回。

    三步按模块头那两条规则的顺序执行；任一步失败 → 先把已改的还原、再抛
    :class:`DeclarationSyncError`（消息带 verifier 的输出尾部，能直接照着修）。调用方只需要收回
    项目树。

    传进来的 ``dir_name`` 是项目树的**目录名**（``ProjectTreeWrite.dir_path.name``，已经按盘上
    真名取过大小写），不是注册键、不是显示名。
    """
    root = resolve_root(mind_root)
    if root is None:
        raise DeclarationSyncError(
            "Mind 够不着（MIND_ROOT 指不到一个库），两处声明同步不了"
        )

    # 安全边界（裁定 72 尾巴① / REQUIREMENTS R-013 边角②）：脑仓自己的 verifier 会把
    # `git rev-parse --show-toplevel` 的结果当 REPO 审。所以 MIND_ROOT 若是「自己不是仓、
    # 又嵌在别的仓里」的一块目录，这一步就会拿**外层代码仓**当脑仓——还会往它里面改
    # AGENTS.md 与 INDEX.md。真脑仓自带 .git（toplevel == root），不受影响。
    # 故意不在「哪都不在仓里」时拦：那是没有可误认的外层仓的正常形态（假库 / 裸目录）。
    enclosing = git_toplevel(root)
    if enclosing is not None:
        try:
            same_repo = Path(enclosing).resolve() == root.resolve()
        except OSError as exc:
            raise DeclarationSyncError(
                f"没法确认 {root} 是不是脑仓自己的顶层（解析路径失败：{exc}）——"
                f"不敢拿别的仓当脑仓同步声明"
            ) from exc
        if not same_repo:
            raise DeclarationSyncError(
                f"{root} 不是 git 仓自己的顶层，而是嵌在 {enclosing} 里面——"
                f"脑仓门禁会把那个外层仓当成本仓审，还会往它里面改 "
                f"{AGENTS_RELATIVE} 与 {INDEX_RELATIVE}。"
                f"要么把 MIND_ROOT 指到一个自带 .git 的脑仓，要么把脑挪出那个仓。"
            )

    name = (dir_name or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise DeclarationSyncError(
            f"{dir_name!r} 不是一个 Mind 项目目录名，声明无从对齐"
        )

    agents_path = root / AGENTS_RELATIVE
    verifier_path = root / VERIFIER_RELATIVE
    index_path = root / INDEX_RELATIVE

    if not agents_path.is_file() and not verifier_path.is_file():
        # 这个 Mind 根不带脑仓自己的门禁（只有 Vault/projects 的假库 / 非 Mind 仓）：没有声明可
        # 同步。此处**不许**退化成「自己写一套 INDEX / 自己造一份 AGENTS.md」——那正是脑仓门禁与
        # 磁盘长期不一致的来源。
        logger.info(
            "mind: %s 没有 %s / %s，项目 %s 只落盘上不落声明",
            root, AGENTS_RELATIVE, VERIFIER_RELATIVE, name,
        )
        return DeclarationSync(
            dir_name=name,
            skipped=(
                f"这个 Mind 库没有 {AGENTS_RELATIVE} 与 {VERIFIER_RELATIVE}，"
                "没有声明要同步"
            ),
            root=root,
        )

    if not verifier_path.is_file():
        raise DeclarationSyncError(
            f"{root} 带着 {AGENTS_RELATIVE}，却没有脑仓自己的 {VERIFIER_RELATIVE}："
            "Vault/projects/INDEX.md 只由那个脚本的 --fix-projects 生成，不另写一套。"
            "先把 Mind 装齐（或把 MIND_ROOT 指回真的脑仓）再建项目分身。"
        )

    names = project_dir_names(root)
    if name not in names:
        # 树建在了别的根上 / 目录被移走：现在对齐的是「这个根」的清单，不是我们这个项目。
        raise DeclarationSyncError(
            f"{PROJECTS_RELATIVE}/{name} 不在 {root} 的盘上，声明对不了盘"
        )

    snapshot: dict[str, bytes | None] = {}
    updated_line = False
    regenerated = False
    try:
        with mind_write_lock(root / LOCK_RELATIVE, timeout=_LOCK_TIMEOUT_SECONDS):
            # ① AGENTS.md §1 那一行（计数 + 列表，都按盘上真名）
            updated_line = _update_agents_line(
                agents_path=agents_path, names=names, snapshot=snapshot
            )
            # ② Vault/projects/INDEX.md —— 脑仓自己的生成器，不是我们写的
            regenerated = _regenerate_index(
                root=root, index_path=index_path, snapshot=snapshot
            )
            # ③ 脑仓自己的门禁：退出码 0 才算这次立项合格
            _assert_gate(root)
    except (DeclarationSyncError, MindLockTimeout) as exc:
        _restore_declarations(root, snapshot)
        if isinstance(exc, MindLockTimeout):
            raise DeclarationSyncError(
                f"Mind 的写锁 {LOCK_RELATIVE} 拿不到（{exc}），两处声明没同步"
            ) from exc
        raise

    if not updated_line and agents_path.is_file():
        logger.warning(
            "mind: %s 里没找到 §1 项目清单声明行，项目 %s 只落了 INDEX 没落声明",
            AGENTS_RELATIVE, name,
        )

    return DeclarationSync(
        dir_name=name,
        updated_agents_line=updated_line,
        regenerated_index=regenerated,
        root=root,
        restored=tuple(snapshot.items()),
    )


__all__ = [
    "AGENTS_RELATIVE",
    "DeclarationSync",
    "DeclarationSyncError",
    "INDEX_RELATIVE",
    "PROJECTS_RELATIVE",
    "VERIFIER_RELATIVE",
    "declared_projects",
    "project_dir_names",
    "render_declaration",
    "sync_project_declarations",
]
