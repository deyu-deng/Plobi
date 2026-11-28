"""say/ask 的 `to` 参数必须带**动态 enum**——派工的人得看得见自己能派给谁。

判据（R-112 / WP-SAY-RECIPIENT-ENUM，裁定 237）：
a. `say` 的 `to` schema 含 enum，值 = 当刻 ``live_names()`` ＋「用户／主人／我／user」。
b. `ask` 的 `to` schema 同样含 enum。
c. ``description`` 里能读到每个候选的 **display_name 与 category**。
d. **动态性**：名册多一行 ⇒ enum 多一个（只 monkeypatch，**不许改 projects.yaml**）。
e. **空名册**：下发的 ``to`` 里**不许出现 ``"enum": []``**。
f. **向后兼容**：别名仍能解析、归档名仍被拒。

这里**不加载** ``plobi-core/__init__.py``——那一格是别的刀在飞（跨线相交）；
只把 ``channel.py`` 当作一个包里的模块装进来。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest


def _plugin_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "plugins" / "plobi-core"


def _load_channel():
    """只装 ``channel.py`` 本身（不执行 plobi-core/__init__.py）。"""
    root = "plobi_plugins"
    if root not in sys.modules:
        ns = types.ModuleType(root)
        ns.__path__ = []  # type: ignore[attr-defined]
        sys.modules[root] = ns
    pkg = "plobi_plugins.plobi_core"
    if pkg not in sys.modules:
        ns = types.ModuleType(pkg)
        ns.__path__ = [str(_plugin_dir())]  # type: ignore[attr-defined]
        sys.modules[pkg] = ns
    name = f"{pkg}.channel"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _plugin_dir() / "channel.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class _Row:
    name: str
    display_name: str = ""
    category: str = ""
    archived: bool = False


@dataclass
class _Registry:
    rows: list[_Row] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.agents = {row.name: row for row in self.rows}

    def get(self, name: str):
        return self.agents.get(name)


@pytest.fixture()
def channel():
    return _load_channel()


@pytest.fixture(autouse=True)
def _restore_schema(channel):
    """`SAY_SCHEMA`/`ASK_SCHEMA` 是模块级对象、就地改写 ⇒ 每个测试前后复原。"""
    saved = []
    for schema in (channel.SAY_SCHEMA, channel.ASK_SCHEMA):
        to = schema["parameters"]["properties"]["to"]
        saved.append((to, dict(to)))
    yield
    for to, original in saved:
        to.clear()
        to.update(original)


@pytest.fixture()
def roster(channel, monkeypatch):
    """假名册：改 `live_names()`（枚举的来源）与 `_roster()`（显示名／分类的来源）。

    **不碰 projects.yaml**——真名册一个字节都不动。
    """
    state = {"rows": [], "live": []}

    def _install(rows):
        state["rows"] = rows
        state["live"] = [r.name for r in rows if not r.archived]
        monkeypatch.setattr(channel, "_roster", lambda: _Registry(rows))
        monkeypatch.setattr(channel, "live_names", lambda: list(state["live"]))
        return state

    state["_install"] = _install  # type: ignore[attr-defined]
    return state


def _to_prop(channel, schema_name: str) -> dict:
    schema = channel.SAY_SCHEMA if schema_name == "say" else channel.ASK_SCHEMA
    return schema["parameters"]["properties"]["to"]


# ── a ──────────────────────────────────────────────────────────────────────


def test_say_to_schema_carries_enum_of_live_names_plus_user_targets(channel, roster):
    roster["_install"]([
        _Row("aura", "Aura", "butler"),
        _Row("plobi-desktop", "Plobi-Desktop", "projects"),
    ])

    channel.check_channel_available()

    to = _to_prop(channel, "say")
    assert "enum" in to, "say 的 `to` 必须带 enum——派工的人得看得见自己能派给谁"
    assert set(to["enum"]) == {"aura", "plobi-desktop", "用户", "主人", "我", "user"}


# ── b ──────────────────────────────────────────────────────────────────────


def test_ask_to_schema_carries_enum_too(channel, roster):
    roster["_install"]([
        _Row("aura", "Aura", "butler"),
        _Row("plobi-desktop", "Plobi-Desktop", "projects"),
    ])

    channel.check_channel_available()

    to = _to_prop(channel, "ask")
    assert "enum" in to, "ask 的 `to` 必须带 enum"
    assert set(to["enum"]) == {"aura", "plobi-desktop", "用户", "主人", "我", "user"}


# ── c ──────────────────────────────────────────────────────────────────────


def test_to_description_reads_display_name_and_category(channel, roster):
    roster["_install"]([
        _Row("aura", "Aura", "butler"),
        _Row("plobi-desktop", "Plobi-Desktop", "projects"),
    ])

    channel.check_channel_available()

    for schema_name in ("say", "ask"):
        description = _to_prop(channel, schema_name)["description"]
        assert "plobi-desktop（Plobi-Desktop · projects）" in description
        assert "aura（Aura · butler）" in description


# ── d ──────────────────────────────────────────────────────────────────────


def test_enum_follows_the_roster_when_it_grows(channel, roster):
    roster["_install"]([
        _Row("aura", "Aura", "butler"),
        _Row("plobi-desktop", "Plobi-Desktop", "projects"),
    ])
    channel.check_channel_available()
    before = list(_to_prop(channel, "say")["enum"])
    assert "newcomer" not in before

    # 名册多一行 ⇒ 枚举多一个（**只 monkeypatch**，projects.yaml 一个字节都不动）
    roster["_install"]([
        _Row("aura", "Aura", "butler"),
        _Row("plobi-desktop", "Plobi-Desktop", "projects"),
        _Row("newcomer", "Newcomer", "butler"),
    ])

    assert channel.check_channel_available() is True

    after = _to_prop(channel, "say")["enum"]
    assert "newcomer" in after
    assert len(after) == len(before) + 1
    assert set(after) == {"aura", "plobi-desktop", "newcomer", "用户", "主人", "我", "user"}


# ── e ──────────────────────────────────────────────────────────────────────


def test_empty_roster_never_ships_an_empty_enum(channel, roster):
    roster["_install"]([
        _Row("aura", "Aura", "butler"),
        _Row("plobi-desktop", "Plobi-Desktop", "projects"),
    ])
    channel.check_channel_available()
    assert _to_prop(channel, "say")["enum"], "前置：先有一份非空枚举"

    roster["_install"]([])

    assert channel.check_channel_available() is False

    for schema_name in ("say", "ask"):
        to = _to_prop(channel, schema_name)
        assert to.get("enum") != [], f"{schema_name} 的 `to` 不许下发空枚举"
        assert '"enum": []' not in json.dumps(
            channel.SAY_SCHEMA if schema_name == "say" else channel.ASK_SCHEMA,
            ensure_ascii=False,
        )


# ── f ──────────────────────────────────────────────────────────────────────


def test_alias_still_resolves_and_archived_row_is_refused(channel, roster, monkeypatch):
    from plobi.agents import identity_aliases

    roster["_install"]([
        _Row("plobi-desktop", "Plobi-Desktop", "projects"),
        _Row("gone-one", "Gone One", "butler", archived=True),
    ])
    monkeypatch.setattr(
        identity_aliases,
        "resolve_identity_alias",
        lambda name: "plobi-desktop" if name == "旧写法" else name,
    )

    entry, refusal = channel.resolve_recipient("旧写法")
    assert refusal is None, f"别名仍要能解析到人：{refusal}"
    assert entry is not None and entry.name == "plobi-desktop"

    entry, refusal = channel.resolve_recipient("gone-one")
    assert entry is None
    assert refusal and "归档" in refusal
