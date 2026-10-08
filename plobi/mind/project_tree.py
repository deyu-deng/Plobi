"""The Mind project tree a project 分身 is supposed to come with.

用户 2026-10-06 的要求（R-013 扩写）：「每创建一个 project 类型的 L2 agent，mind 里面
要自动创建对应的文件夹和相关文件。如果有出现重名的就直接禁止创建。」这里只负责「建」
的那一半——照 ``Vault/projects/`` 里活项目的形状生成目录 + 三件套；「禁止重名」的判据
在 :func:`plobi.agents.registry.registration_blocker`，两处不许各写一套。

形状是抄来的，不是发明的（Mind ``AGENTS.md`` §4 场景二：按用户给定名称建目录，初始化
``plan.md`` / ``progress.md`` / ``research.md`` 三件套）：

* ``plan.md`` 的 frontmatter 键 = Mind 的 ``Loom/scripts/verifier.py`` 重生成
  ``Vault/projects/INDEX.md`` 时实际读的那几个（project / title / type / group / cloud /
  status / created / updated / tech / summary），段落取活项目里公认的那七节
  （Goal / Roadmap / Milestones / Status / Decisions / Risks / References）。
* ``cloud`` 写项目名本身、不写盘符——2026-09-28 那轮元数据修正（见 Nymo / Personal-Website
  的 ``progress.md`` 留痕）把设备事实从这一格里清出去了，新建的格子里不能再塞回去。
* Mind ``AGENTS.md`` §5「Vault 禁止 AI 元注释」：生成体里不写「最后更新：」「来源：」。

本模块自己不碰的两样（同一次创建里由 :mod:`plobi.mind.project_declarations` 紧跟着办，
Mind ``AGENTS.md`` §6「结构变更后必须更新本文件 §1」那条现在落到工具上）：
``Vault/projects/INDEX.md``（只由 Mind 自己的 ``verifier.py --fix-projects`` 生成，不另写一套）
和 Mind ``AGENTS.md`` §1 的项目清单声明。少了这两步，脑仓自己的 verifier 会把这次立项判成
BLOCKER（声明 ≠ 磁盘），用户在脑仓的下一次提交就被他自己装的 pre-commit 拦住。

写入不走 :func:`plobi.mind.paths.safe_target`：那份前缀表管的是**AI 随笔**能落哪（memory
adapter 只准往 Loom 消化区写字），这里落的是用户点「新建项目分身」时要求建的立项卡。
约束换成两条更硬的：目标必须解析进 ``<mind_root>/Vault/projects/`` 之内（防穿越），
已有文件一个字都不覆盖（重名走禁止，不走覆盖）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Sequence

from .paths import resolve_root

PROJECTS_RELATIVE = "Vault/projects"
ENTRY_FILE_NAMES: tuple[str, ...] = ("plan.md", "progress.md", "research.md")

# registry category -> (plan.md ``type``, plan.md ``group``)。活项目里 engineering/product
# 与 research/research 两种配对（Aura/Kit/Nymo vs Wealth-Lab/SRTP），没有第三种。
_CATEGORY_TO_PLAN_META: dict[str, tuple[str, str]] = {
    "projects": ("engineering", "product"),
    "research": ("research", "research"),
}


class ProjectTreeConflict(RuntimeError):
    """这棵树不能被这条分身领走（路径上是个文件 / 名字被占）——禁止创建。"""


class ProjectTreeUnwritable(RuntimeError):
    """Mind 够不着或写不下去——按「建不成就算创建失败」处理，调用方回滚注册表。"""


@dataclass(frozen=True)
class ProjectTreeWrite:
    """一次立项的落地结果，带着「怎么撤销自己」的账。

    ``reused`` = 磁盘上早有这棵树（含 ``plan.md``），我们一个字没写；``relative`` 用的是
    **磁盘上真实的那个名字**，不是我们传进去的大小写——不分大小写的盘上 ``Vault/projects/Nymo``
    和请求里的 ``nymo`` 是同一个目录，注册表里必须记那一个名字，否则下一轮播种机认不出它。
    """

    relative: str
    dir_path: Path
    reused: bool = False
    created_dir: bool = False
    created_files: tuple[Path, ...] = ()

    def rollback(self) -> None:
        """只撤自己写过的东西：删自己建的文件，目录是自己建且空了才删。

        绝不 rmtree——``created_dir=False`` 的目录里可能装着用户的资料。
        """
        for path in self.created_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if self.created_dir:
            try:
                if self.dir_path.is_dir() and not any(self.dir_path.iterdir()):
                    self.dir_path.rmdir()
            except OSError:
                pass


def _today_iso() -> str:
    return _date.today().isoformat()


def _yaml_inline_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(str(v).strip() for v in values if str(v).strip()) + "]"


def plan_document(
    *,
    title: str,
    summary: str,
    category: str,
    tech: Sequence[str] = (),
    today: str = "",
) -> str:
    """新建项目 ``plan.md`` 的全文（frontmatter + 七节骨架）。"""
    day = today or _today_iso()
    plan_type, group = _CATEGORY_TO_PLAN_META.get(category, ("engineering", "product"))
    safe_title = title.replace("---", "- - -")
    # summary 里若有换行会撕开 frontmatter 那一格，压成一行。
    one_line = " ".join((summary or "").split())
    return (
        "---\n"
        f"project: {safe_title}\n"
        f"title: {safe_title}\n"
        f"type: {plan_type}\n"
        f"group: {group}\n"
        f"cloud: {safe_title}\n"
        "status: active\n"
        f"created: {day}\n"
        f"updated: {day}\n"
        f"tech: {_yaml_inline_list(tech)}\n"
        f"summary: {one_line}\n"
        "---\n"
        "\n"
        f"# {safe_title}\n"
        "\n"
        f"> {one_line}\n"
        "\n"
        "## Goal\n"
        "\n"
        "（待补：这个项目要达成什么。）\n"
        "\n"
        "## Roadmap\n"
        "\n"
        "| # | 阶段 | 交付标准 |\n"
        "|---|------|----------|\n"
        "| 1 | 立项 | 目标与第一阶段交付写清楚 |\n"
        "\n"
        "## Milestones\n"
        "\n"
        "- [ ] M1：明确目标与第一个可展示的闭环\n"
        "\n"
        "## Status\n"
        "\n"
        "- 当前阶段：立项\n"
        "- 下一步：（待补）\n"
        "\n"
        "## Decisions\n"
        "\n"
        "- （待补充）\n"
        "\n"
        "## Risks\n"
        "\n"
        "- （待补充）\n"
        "\n"
        "## References\n"
        "\n"
        "- （待补充）\n"
    )


def progress_document(*, title: str, today: str = "") -> str:
    """新建项目 ``progress.md`` 的全文（Status / Sprint / Log 三段，形状同 Nymo）。"""
    day = today or _today_iso()
    return (
        f"# {title} · 进度\n"
        "\n"
        "## Status\n"
        "\n"
        "- 阶段：立项\n"
        "- 进行中：—\n"
        "- 阻塞：无\n"
        "- 下一步：—\n"
        "\n"
        "## Sprint\n"
        "\n"
        "| 日期 | 任务 | 状态 |\n"
        "|------|------|------|\n"
        "| — | 明确目标与第一阶段交付 | ⬜ |\n"
        "\n"
        "## Log\n"
        "\n"
        f"- {day}：建档（Plobi 登记项目分身时自动建的三件套）\n"
    )


def research_document(*, title: str) -> str:
    return (
        f"# {title} · 调研\n"
        "\n"
        "（按日期倒序。竞品、选型、试错记录随项目推进补充。）\n"
    )


def entry_files(
    *, title: str, summary: str, category: str, today: str = ""
) -> dict[str, str]:
    """三件套的文件名 → 内容（键顺序与 :data:`ENTRY_FILE_NAMES` 一致）。"""
    return {
        "plan.md": plan_document(
            title=title, summary=summary, category=category, today=today
        ),
        "progress.md": progress_document(title=title, today=today),
        "research.md": research_document(title=title),
    }


def _case_insensitive_child(base: Path, name: str) -> str:
    """``base`` 下与 *name* 只差大小写的真实目录名（没有则空串）。

    不分大小写的盘上 ``Path("nymo")`` 会解析进 ``Nymo/``，注册表里却会记下 ``nymo``
    这个不存在的名字——下一轮 :func:`plobi.agents.registry.ensure_mind_project_agents`
    扫的是磁盘真名，于是同一棵树又被登记成第二条。所以永远以磁盘上那个名字为准。
    """
    try:
        children = list(base.iterdir())
    except OSError:
        return ""
    for child in children:
        if child.is_dir() and child.name != name and child.name.lower() == name.lower():
            return child.name
    return ""


def _containment_ok(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def ensure_project_tree(
    *,
    raw_name: str,
    summary: str = "",
    category: str = "projects",
    mind_root: Path | str | None = None,
    today: str = "",
) -> ProjectTreeWrite:
    """在 ``<mind_root>/Vault/projects/<目录名>`` 建出三件套，返回这次写了什么。

    三条规则：够不着 Mind 就抛（调用方按创建失败处理，不留孤儿行）；已有 ``plan.md``
    的树只**认领、不覆盖**（``reused=True``，一个字都不写）；目录已存在但没有 ``plan.md``
    （Mind 自己的 verifier 会把它报成「幽灵目录」）时只补缺的那几份，已有的文件不动。

    目录名走 :func:`plobi.agents.registry._project_safe_id`——播种机
    ``ensure_mind_project_agents`` 就是拿它把 Mind 目录名归成注册键的，两边共用同一个
    归一函数才认得出「这是同一条」；自己另写一套字符规则，等于给同一个项目造出两个名字。
    """
    from plobi.agents.registry import _project_safe_id  # lazy: registry 不依赖本模块

    raw = (raw_name or "").strip()
    if not raw:
        # 归一函数对空串也会吐一个 proj-<摘要> 兜底名，那在这儿等于凭空造项目。
        raise ProjectTreeConflict("项目名是空的，建不出 Mind 项目目录")
    dir_name = _project_safe_id(raw)

    root = resolve_root(mind_root)
    if root is None:
        raise ProjectTreeUnwritable(
            "Mind 没配置好（MIND_ROOT 指不到一个库），项目树建不出来"
        )

    base = root / PROJECTS_RELATIVE
    actual_name = _case_insensitive_child(base, dir_name) or dir_name
    target = base / actual_name
    # frontmatter 的 project/title 用用户给定的原名（Mind §5：目录名尊重用户给定名称）；
    # 只有目录名才被归一成 profile-safe 的形式。
    title = raw

    if not _containment_ok(base, target):
        raise ProjectTreeConflict(f"{dir_name!r} 会落到 {PROJECTS_RELATIVE} 之外，禁止创建")
    if target.exists() and not target.is_dir():
        raise ProjectTreeConflict(
            f"{PROJECTS_RELATIVE}/{actual_name} 位置上是个文件，建不出项目目录"
        )

    if target.is_dir() and (target / "plan.md").is_file():
        return ProjectTreeWrite(
            relative=f"{PROJECTS_RELATIVE}/{actual_name}",
            dir_path=target,
            reused=True,
        )

    created_dir = not target.exists()
    written: list[Path] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        files = entry_files(title=title, summary=summary, category=category, today=today)
        for name in ENTRY_FILE_NAMES:
            path = target / name
            if path.exists():
                continue  # 已有内容一个字不改
            path.write_text(files[name], encoding="utf-8")
            written.append(path)
    except OSError as exc:
        # 半途失败也要把自己刚建的东西收干净，别给 Mind 留一堆残骸。
        ProjectTreeWrite(
            relative=f"{PROJECTS_RELATIVE}/{actual_name}",
            dir_path=target,
            created_dir=created_dir,
            created_files=tuple(written),
        ).rollback()
        raise ProjectTreeUnwritable(f"Mind 项目树写不下去：{exc}") from exc

    return ProjectTreeWrite(
        relative=f"{PROJECTS_RELATIVE}/{actual_name}",
        dir_path=target,
        created_dir=created_dir,
        created_files=tuple(written),
    )


def project_tree_exists(*, mind_subtree: str, mind_root: Path | str | None = None) -> bool:
    """注册表这一格的 ``mind_subtree`` 指的是一个真有 ``plan.md`` 的目录吗。"""
    if not (mind_subtree or "").strip():
        return False
    root = resolve_root(mind_root)
    if root is None:
        return False
    return (root / mind_subtree / "plan.md").is_file()


__all__ = [
    "ENTRY_FILE_NAMES",
    "PROJECTS_RELATIVE",
    "ProjectTreeConflict",
    "ProjectTreeUnwritable",
    "ProjectTreeWrite",
    "entry_files",
    "ensure_project_tree",
    "plan_document",
    "project_tree_exists",
]
