"""插件能声明自己填进界面挂载点（slot）里的东西（裁定 239 第二刀）。

浅的那一步是**声明式**：插件在 ``plugin.yaml`` 的 ``slots:`` 一节里声明「填哪个 slot、
带什么内容」，后端把它们收集起来给前端——**插件不在界面上执行代码**（那要打包链＋沙箱＋
权限模型，是"深"的那一步）。
"""

import types

import pytest


def _manifest(**kwargs):
    from plobi_cli.plugins import PluginManifest

    return PluginManifest(name="my-thing", key="my-thing", **kwargs)


def test_manifest_keeps_the_declared_slots(tmp_path):
    from plobi_cli.plugins import get_plugin_manager

    plugin_dir = tmp_path / "my-thing"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: my-thing\n"
        "kind: standalone\n"
        "slots:\n"
        "  - slot: sidebar/top/my-thing\n"
        "    id: my-thing\n"
        "    icon: star\n"
        "    label: 我的那一格\n",
        encoding="utf-8",
    )

    manifest = get_plugin_manager()._parse_manifest(plugin_dir / "plugin.yaml", plugin_dir, "user", "")

    assert manifest is not None
    assert manifest.slots, "the manifest dropped the declared slot"
    assert manifest.slots[0]["slot"] == "sidebar/top/my-thing"


def test_contributions_carry_what_the_plugin_declared(monkeypatch):
    from plobi_cli import plugins

    manifest = _manifest(
        slots=[{"slot": "sidebar/top/my-thing", "id": "my-thing", "icon": "star", "label": "我的那一格"}]
    )
    fake = types.SimpleNamespace(
        _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
        _plugin_tool_names=set(),
    )
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)

    out = plugins.get_plugin_slot_contributions()

    assert len(out) == 1
    row = out[0]
    assert row["slotId"] == "sidebar/top/my-thing"
    assert row["id"] == "my-thing"
    assert row["icon"] == "star"
    assert row["label"] == "我的那一格"
    assert row["providedBy"] == "my-thing"


def test_a_contribution_carries_the_slot_shape_it_declares(monkeypatch):
    """插件**自己声明**一个新 slot 时（"我要在工作区多一个页签"），那几样也得带出来：
    位置、区域、顺序、形状——宿主只认自己声明过的位置，但形状由插件给。"""
    from plobi_cli import plugins

    manifest = _manifest(
        slots=[
            {
                "slot": "workspace/tabs/my-tab",
                "position": "workspace",
                "region": "tabs",
                "order": 70,
                "id": "my-tab",
                "icon": "star",
                "label": "我的那一格",
                "view": "knowledge",
            }
        ]
    )
    fake = types.SimpleNamespace(
        _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
        _plugin_tool_names=set(),
    )
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)

    row = plugins.get_plugin_slot_contributions()[0]

    assert row["slotId"] == "workspace/tabs/my-tab"
    assert row["position"] == "workspace"
    assert row["region"] == "tabs"
    assert row["order"] == 70
    assert row["view"] == "knowledge"


def test_a_contribution_carries_the_search_words_it_declared(monkeypatch):
    """插件加的那一格**在命令面板里搜得到** ⇒ 它声明的那几个搜索词得带出来。

    那一列（命令面板「跳到某一处」）是从声明表派生的：插件自己声明的搜索词与快捷键
    action id 一并带过去，宿主不替它猜。
    """
    from plobi_cli import plugins

    manifest = _manifest(
        slots=[
            {
                "slot": "sidebar/top/my-thing",
                "id": "my-thing",
                "icon": "star",
                "label": "我的那一格",
                "route": "/my-thing",
                "keywords": ["my thing", "my-words", "  ", 42],
                "action": "nav.my-thing",
            }
        ]
    )
    fake = types.SimpleNamespace(
        _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
        _plugin_tool_names=set(),
    )
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)

    row = plugins.get_plugin_slot_contributions()[0]

    # 非字符串的与空的那些**不许**带过去（前端那一列搜的是词，不是杂货）。
    assert row["keywords"] == ["my thing", "my-words"]
    assert row["action"] == "nav.my-thing"


def test_a_declaration_without_a_target_is_ignored(monkeypatch):
    """没写填哪个 slot 的那一条不算数——宿主不认没指定位置的贡献。"""
    from plobi_cli import plugins

    manifest = _manifest(slots=[{"id": "my-thing", "icon": "star"}])
    fake = types.SimpleNamespace(
        _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
        _plugin_tool_names=set(),
    )
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)

    assert plugins.get_plugin_slot_contributions() == []


def test_the_manifest_keeps_the_declared_config_fields(tmp_path):
    """插件声明自己那几个配置字段（设置屏那一族：一段＝一组配置字段）。"""
    from plobi_cli.plugins import get_plugin_manager

    plugin_dir = tmp_path / "my-thing"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: my-thing\n"
        "kind: standalone\n"
        "config:\n"
        "  - key: api_key\n"
        "    label: API key\n"
        "    type: string\n"
        "    description: 用来连我的服务\n",
        encoding="utf-8",
    )

    manifest = get_plugin_manager()._parse_manifest(plugin_dir / "plugin.yaml", plugin_dir, "user", "")

    assert manifest is not None
    assert manifest.config, "the manifest dropped the declared config fields"
    assert manifest.config[0]["key"] == "api_key"


def test_a_settings_section_carries_the_fields_the_plugin_declared(monkeypatch):
    """设置屏那一段＝一组配置字段 ⇒ 那一段带的是**插件自己声明的那几个字段**。

    键是**完整的配置键**（`plugins.entries.<plugin_key>.<field>`）——与插件自己读配置的
    那一处同一处 ⇒ 用户在屏上改的值，插件读得到。
    """
    from plobi_cli import plugins

    manifest = _manifest(
        slots=[
            {
                "slot": "settings/sections/my-section",
                "position": "settings",
                "region": "sections",
                "kind": "settings-section",
                "id": "my-section",
                "label": "我的一段",
            }
        ],
        config=[{"key": "api_key", "label": "API key", "type": "string"}],
    )
    fake = types.SimpleNamespace(
        _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
        _plugin_tool_names=set(),
    )
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)

    row = plugins.get_plugin_slot_contributions()[0]

    assert row["position"] == "settings"
    assert row["region"] == "sections"
    assert row["keys"] == ["plugins.entries.my-thing.api_key"]


def test_the_declared_config_fields_become_schema_entries(monkeypatch):
    """插件声明的字段**并进配置 schema** ⇒ 屏上那一份通用表单直接渲染它。"""
    from plobi_cli import plugins

    manifest = _manifest(
        config=[
            {"key": "api_key", "label": "API key", "type": "string", "description": "连我的服务"},
            {"key": "mode", "label": "Mode", "type": "select", "options": ["a", "b"]},
        ]
    )
    fake = types.SimpleNamespace(
        _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
        _plugin_tool_names=set(),
    )
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)

    fields = plugins.get_plugin_config_fields()

    key = "plugins.entries.my-thing.api_key"
    assert key in fields
    assert fields[key]["label"] == "API key"
    assert fields[key]["description"] == "连我的服务"
    assert fields[key]["type"] == "string"
    # 给了选项的那一条就是下拉。
    mode = fields["plugins.entries.my-thing.mode"]
    assert mode["type"] == "select"
    assert mode["options"] == ["a", "b"]


class TestSlotsEndpoint:
    """``GET /api/slots`` 把插件的声明交给前端。"""

    @pytest.fixture(autouse=True)
    def _client(self, monkeypatch, _isolate_plobi_home):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/starlette not installed")

        import plobi_state
        from plobi_constants import get_plobi_home
        from plobi_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

        monkeypatch.setattr(plobi_state, "DEFAULT_DB_PATH", get_plobi_home() / "state.db")
        self.client = TestClient(app)
        self.client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

    def test_it_returns_the_contributions(self, monkeypatch):
        from plobi_cli import plugins

        manifest = _manifest(
            slots=[{"slot": "sidebar/top/my-thing", "id": "my-thing", "icon": "star", "label": "我的那一格"}]
        )
        fake = types.SimpleNamespace(
            _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
            _plugin_tool_names=set(),
        )
        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)

        resp = self.client.get("/api/slots")

        assert resp.status_code == 200
        data = resp.json()
        assert data["contributions"][0]["slotId"] == "sidebar/top/my-thing"
        assert data["contributions"][0]["id"] == "my-thing"

    def test_it_survives_a_broken_plugin(self, monkeypatch):
        """插件炸了不许把整屏带下去——但也要出声（记进日志），不许静悄悄消失。"""
        from plobi_cli import plugins

        def _boom():
            raise RuntimeError("plugin exploded")

        monkeypatch.setattr(plugins, "get_plugin_slot_contributions", _boom)

        resp = self.client.get("/api/slots")

        assert resp.status_code == 200
        assert resp.json()["contributions"] == []
