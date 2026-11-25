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
