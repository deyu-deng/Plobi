"""命令行那句话不再教「profile」这个概念（WP-PROFILE-CANCEL 第六轮）。

判的是**读得到的那一句**：``help=`` / ``description=`` / ``metavar=`` 三类字符串，和
位置参数在 usage 行里印出来的名字。用户手打 ``plobi agent --help``、外部 Agent 读
``--help`` 当说明书，这两拨读者看见的词就是产品的口径。

不在这儿判的四样，都是「改名要动数据 / 会砸到别人的在飞改动」，判据与次序在
``Docs/tasks/wp-profile-cancel-task.md`` §4 排着：

* ``profile_name`` 这类 dest —— Python 标识符，屏上看不见（看得见的用法行由 metavar 管）
* ``PLOBI_PROFILE`` / ``PLOBI_PROFILE_NAME`` 环境变量 —— 外部脚本还在传，读它们的回落没撤
* ``plobi_cli/profiles.py`` 模块名 —— 建家与改名的库本体，别的线还在 import 它
* ``notifier_profile`` / ``task_runs.profile`` 这些库里的列名 —— 迁移要碰数据

牙的形状：新写一句带那个词的帮助，或者新加一个印着 ``profile_name`` 的位置参数，
本档当场红。豁免按 ``(文件, 字样)`` 登记，每条都得写理由。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# 命令行面上「人读的那一句」住在这几档里。
SCAN_FILES = (
    "plobi_cli/subcommands/profile.py",
    "plobi_cli/subcommands/plobi.py",
    "plobi_cli/subcommands/gateway.py",
    "plobi_cli/subcommands/skills.py",
    "plobi_cli/subcommands/plugins.py",
    "plobi_cli/kanban.py",
    "plobi/agents/cli.py",
)

# (文件, 允许出现的字样, 理由)。理由写不全就别登记。
EXEMPT = (
    (
        "plobi_cli/subcommands/plugins.py",
        "chrome-profiles",
        "那是别人仓的真名（anpicasso/plobi-plugin-chrome-profiles），不是我们的概念",
    ),
)

WORD = "profile"
# 位置参数印出来的旧门牌号：usage 行里会露概念的两个名字。
OLD_POSITIONAL_NAMES = frozenset({"profile", "profile_name", "active_profile"})


def _literal_text(node: ast.AST) -> str | None:
    """把 help/description 那种字符串字面量（含隐式拼接与 f-string）拍平成一个串。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal_text(part) or "" for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (_literal_text(node.left) or "") + (_literal_text(node.right) or "")
    return None


def _calls(source: str) -> list[ast.Call]:
    tree = ast.parse(source)
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def help_strings(source: str) -> list[tuple[int, str]]:
    """``(行号, 那句话)``——取 help= / description= / metavar= 三类的值。

    metavar 也算屏上的字：``--worker PROFILE:TITLE`` 那一样印在 ``--help`` 里，
    和 help= 一样是说明书。
    """
    found: list[tuple[int, str]] = []
    for call in _calls(source):
        for kw in call.keywords:
            if kw.arg in ("help", "description", "metavar"):
                text = _literal_text(kw.value)
                if text:
                    found.append((call.lineno, text))
    return found


def positional_names(source: str) -> list[tuple[int, str]]:
    """``add_argument`` 里那些**位置参数**的名字（不带 - 的第一个实参）。"""
    out: list[tuple[int, str]] = []
    for call in _calls(source):
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if not first.value.startswith("-"):
                out.append((call.lineno, first.value))
    return out


def positional_metavars(source: str) -> dict[str, str]:
    """位置参数名 → metavar 的值（没写 metavar 的不在表里）。"""
    out: dict[str, str] = {}
    for call in _calls(source):
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not call.args:
            continue
        first = call.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if first.value.startswith("-"):
            continue
        for kw in call.keywords:
            if kw.arg == "metavar":
                text = _literal_text(kw.value)
                if text:
                    out[first.value] = text
    return out


def _exempt(rel: str, text: str) -> bool:
    return any(
        file == rel and token in text.casefold() for file, token, _why in EXEMPT
    )


def test_cli_help_never_teaches_the_profile_concept() -> None:
    offenders: list[str] = []
    for rel in SCAN_FILES:
        path = REPO / rel
        assert path.is_file(), f"面变了：{rel} 不在了（这一档被搬走要同步判据）"
        for lineno, text in help_strings(path.read_text(encoding="utf-8")):
            if WORD in text.casefold() and not _exempt(rel, text):
                offenders.append(f"{rel}:{lineno}: {text[:90]}")
    assert not offenders, (
        "这些帮助句子还在把退役的概念教给用户／外部 Agent——"
        "身份是名册里的一行，那句里说的东西请按现在真正发生的事重写：\n  "
        + "\n  ".join(offenders)
    )


def test_usage_lines_do_not_print_the_old_home_name() -> None:
    offenders: list[str] = []
    for rel in SCAN_FILES:
        source = (REPO / rel).read_text(encoding="utf-8")
        metavars = positional_metavars(source)
        for lineno, name in positional_names(source):
            if name in OLD_POSITIONAL_NAMES:
                shown = metavars.get(name)
                if not shown or WORD in shown.casefold():
                    offenders.append(
                        f"{rel}:{lineno}: 位置参数 {name} 在 usage 行里印成 "
                        f"{shown or name}"
                    )
    assert not offenders, (
        "dest 可以留着（那是 Python 标识符），但 usage 行是用户读的——"
        "给这些位置参数加 metavar=\"AGENT\"：\n  " + "\n  ".join(offenders)
    )


def test_the_teeth_bite(tmp_path) -> None:
    """这条不测产品，测判据本身：写成一条新的帮助句子，扫描必须抓到。"""
    sample = (
        "p = sub.add_parser('x', help='Manage things')\n"
        "c = p.add_parser('create', help='Create a new profile')\n"
        "c.add_argument('profile_name', help='Profile to create')\n"
        "p.add_parser('show', description=(\n"
        "    'Show a profile' '\\n' 'and its files'\n"
        "))\n"
    )
    hits = help_strings(sample)
    bad = [text for _lineno, text in hits if WORD in text.casefold()]
    assert len(bad) == 3, f"隐式拼接或 help= 的形状没被认出来：{bad}"
    assert "Create a new profile" in bad
    assert "Profile to create" in bad
    assert any("Show a profile" in text for text in bad)

    names = positional_names(sample)
    assert ("profile_name" in [n for _l, n in names]), names
    assert positional_metavars(sample) == {}, "样例里没写 metavar，判据不该凭空捏一个"
