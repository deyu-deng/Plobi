"""名册行的**域声明**——「这一跳该找谁」的真源（裁定 218）。

这一层只回答一件事：*哪一行声明了自己负责哪个域*。它**不**从话里猜域、
**不**替模型挑人、**不**改写 ``to``。消费点是
``plugins/plobi-core/channel.py::_domain_gate``：发起方显式给了 ``domain``
才查表，没给就完全照旧（fail-open）。

域的取值有两层，零迁移成本：

* ``category``（``registry.AGENT_CATEGORIES`` 的固定枚举）**永远**算一个域；
  存量名册行即使一行没改，也已经按类型声明了域。
* 可选的 ``domains`` 是名册行上的细标签：同一 category 里几个 Agent 分摊
  不同事务时用，写进 ``projects.yaml`` 即可，不需要新造词表。

为什么不从内容反推：``hard_route.py:24-26`` 明写 "There is no keyword
table"，``:72`` 明写「交给哪个 L2 由 ask 内部的名册解析决定」。把散文换成
「关键词 → Agent」硬表，只是把被裁定否掉的软路由搬进 YAML。这里做的是另一件
事：把「谁负责什么」变成**可查的数据**，让程序在派发前强制执行一致性。
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "declared_domains",
    "domain_roster",
    "owners_for_domain",
    "check_recipient",
]


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def declared_domains(entry: Any) -> frozenset[str]:
    """一行的域声明 = ``category`` + 可选 ``domains`` 细标签（去重、归一）。"""
    out: set[str] = set()
    category = _norm(getattr(entry, "category", ""))
    if category:
        out.add(category)
    for raw in getattr(entry, "domains", ()) or ():
        token = _norm(raw)
        if token:
            out.add(token)
    return frozenset(out)


def domain_roster(entries: Iterable[Any]) -> dict[str, list[str]]:
    """整张「域 → 负责人」表。只收未归档、有名字的行，名字稳定排序。

    这是**架构那一半**：表随名册增删自动变，不靠谁记得住「agenda 管日程」。
    """
    table: dict[str, set[str]] = {}
    for entry in entries:
        if getattr(entry, "archived", False):
            continue
        name = str(getattr(entry, "name", "") or "").strip()
        if not name:
            continue
        for domain in declared_domains(entry):
            table.setdefault(domain, set()).add(name)
    return {domain: sorted(names) for domain, names in sorted(table.items())}


def owners_for_domain(domain: str, entries: Iterable[Any]) -> list[str]:
    """声明负责这个域的行；没有就是空表（不猜、不回退到「所有人」）。"""
    return domain_roster(entries).get(_norm(domain), [])


def check_recipient(
    entry: Any, domain: str, entries: Iterable[Any]
) -> tuple[bool, str, list[str]]:
    """权威校验：``to`` 指向的这一行有没有声明负责这个域。

    返回 ``(ok, 一句人话, 该域的权威候选)``。**只校验、不改写**——错配时把
    候选列给发起方，让它自己重填 ``to``；程序绝不替它挑人。多主（一个域几行
    都声明了）也照列，选谁仍由发起方决定。

    没给 ``domain`` ⇒ ``(True, "", [])``：这一层完全不介入，既有行为零变化。
    """
    token = _norm(domain)
    if not token:
        return True, "", []
    owners = owners_for_domain(token, entries)
    name = str(getattr(entry, "name", "") or "").strip()
    if not owners:
        return (
            False,
            f"域「{token}」在名册里还没有任何一行声明负责它；这次先不派，别猜收件人。",
            [],
        )
    if name.casefold() in {owner.casefold() for owner in owners}:
        return True, "", owners
    return (
        False,
        f"「{name}」那一行没有声明负责域「{token}」；这一跳按名册行派，"
        f"请从声明了该域的行里重填 `to`，不要自己挑。",
        owners,
    )
