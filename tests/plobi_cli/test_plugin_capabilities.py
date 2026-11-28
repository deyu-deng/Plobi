"""深档插件的**能力清单**（规格 ``specs/plugin-sandbox.md`` §2.1 · 深档刀 1）。

用户 11-28 14:1x 拍板：深档（带代码的）插件**声明它要什么**，宿主**逐项注入**。
这一刀只管**声明与读出**（窄接口是下一刀）：

- 能力名是**闭集** ⇒ 不认识的名字 ⇒ 那个插件**装不上**，且日志说清是哪个名字；
- **缺边界 ⇒ 那项能力不可放行**（默认拒绝，不是不设限）；
- 浅档插件（没有 ``permissions:``，今天全部）⇒ 照旧加载，清单为空。
"""

import types

from plobi_cli.plugins import PLUGIN_CAPABILITIES, capability_grantable, get_plugin_capabilities


def _manifest(**kwargs):
    from plobi_cli.plugins import PluginManifest

    return PluginManifest(name="my-thing", key="my-thing", **kwargs)


def _fake_manager(monkeypatch, manifest):
    from plobi_cli import plugins

    fake = types.SimpleNamespace(
        _plugins={"my-thing": types.SimpleNamespace(manifest=manifest)},
        _plugin_tool_names=set(),
    )
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: fake)
    return fake


# ── 闭集 ──────────────────────────────────────────────────────────────────────


def test_the_capability_names_are_a_closed_set():
    """宿主认得的能力就那几个 —— 插件不能自己发明一个。"""
    assert "fs.read" in PLUGIN_CAPABILITIES
    assert "net.fetch" in PLUGIN_CAPABILITIES
    assert "ui.render" in PLUGIN_CAPABILITIES
    # 「执行任意代码」不是一项能力（那是要解掉的问题本身，不是能力名）。
    assert "exec" not in PLUGIN_CAPABILITIES
    assert "process.spawn" not in PLUGIN_CAPABILITIES


def test_an_unknown_capability_name_means_the_plugin_is_not_loaded(tmp_path, caplog):
    """不认识的能力名 ⇒ **那个插件装不上**，且**说清是哪个名字**（失败可见）。"""
    from plobi_cli.plugins import get_plugin_manager

    plugin_dir = tmp_path / "my-thing"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: my-thing\n"
        "kind: standalone\n"
        "permissions:\n"
        "  - fs.read\n"
        "  - totally.made.up\n",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        manifest = get_plugin_manager()._parse_manifest(
            plugin_dir / "plugin.yaml", plugin_dir, "user", ""
        )

    assert manifest is None, "带不认识的能力名的插件不该加载"
    assert "totally.made.up" in caplog.text


def test_a_known_capability_name_loads_fine(tmp_path):
    from plobi_cli.plugins import get_plugin_manager

    plugin_dir = tmp_path / "my-thing"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: my-thing\n"
        "kind: standalone\n"
        "permissions:\n"
        "  - fs.read\n"
        "  - net.fetch\n"
        "limits:\n"
        "  net:\n"
        "    hosts: [api.example.com]\n",
        encoding="utf-8",
    )

    manifest = get_plugin_manager()._parse_manifest(
        plugin_dir / "plugin.yaml", plugin_dir, "user", ""
    )

    assert manifest is not None
    assert manifest.permissions == ["fs.read", "net.fetch"]
    assert manifest.limits == {"net": {"hosts": ["api.example.com"]}}


# ── 缺边界 ⇒ 默认拒绝 ─────────────────────────────────────────────────────────


def test_a_capability_without_its_limit_is_not_grantable():
    """``net.fetch`` 不给域名白名单 ⇒ **不给**（不是「全网都能去」）。"""
    assert capability_grantable("net.fetch", {}) is False
    assert capability_grantable("net.fetch", {"net": {}}) is False
    assert capability_grantable("net.fetch", {"net": {"hosts": []}}) is False
    assert capability_grantable("net.fetch", {"net": {"hosts": ["a.example"]}}) is True


def test_file_capabilities_need_paths():
    assert capability_grantable("fs.read", {}) is False
    assert capability_grantable("fs.read", {"fs": {"paths": ["/tmp/x"]}}) is True
    assert capability_grantable("fs.write", {"fs": {"paths": []}}) is False


def test_capabilities_without_a_limit_need_none():
    """没有边界要求的那几项：给了名字就能放行（它们本身不是越界动作）。"""
    assert capability_grantable("config.read", {}) is True
    assert capability_grantable("ui.render", {}) is True


# ── 宿主读得出那一份 ───────────────────────────────────────────────────────────


def test_the_host_reads_what_each_plugin_wants(monkeypatch):
    _fake_manager(
        monkeypatch,
        _manifest(
            permissions=["fs.read", "net.fetch"],
            limits={"net": {"hosts": ["api.example.com"]}},
        ),
    )

    rows = get_plugin_capabilities()

    assert len(rows) == 1
    row = rows[0]
    assert row["key"] == "my-thing"
    assert row["permissions"] == ["fs.read", "net.fetch"]
    assert row["limits"] == {"net": {"hosts": ["api.example.com"]}}
    # 缺 fs.paths ⇒ fs.read 不可放行（默认拒绝）。
    assert row["ungrantable"] == ["fs.read"]


def test_a_shallow_plugin_has_an_empty_capability_list(monkeypatch):
    """浅档插件（今天全部，没有 ``permissions:``）⇒ 清单为空，不进这套机制。"""
    _fake_manager(monkeypatch, _manifest())

    rows = get_plugin_capabilities()

    assert rows[0]["permissions"] == []
    assert rows[0]["limits"] == {}
    assert rows[0]["ungrantable"] == []


# ── 端点 ─────────────────────────────────────────────────────────────────────


class TestCapabilitiesEndpoint:
    """``GET /api/plugin-capabilities`` 把「每个插件要什么」交给前端。"""

    def test_it_returns_the_rows(self, monkeypatch):
        from plobi_cli import plugins

        try:
            from starlette.testclient import TestClient
        except ImportError:
            import pytest

            pytest.skip("fastapi/starlette not installed")

        import plobi_state
        from plobi_constants import get_plobi_home
        from plobi_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

        monkeypatch.setattr(plobi_state, "DEFAULT_DB_PATH", get_plobi_home() / "state.db")
        client = TestClient(app)
        client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

        _fake_manager(monkeypatch, _manifest(permissions=["ui.render"]))

        resp = client.get("/api/plugin-capabilities")

        assert resp.status_code == 200
        data = resp.json()
        assert data["plugins"][0]["permissions"] == ["ui.render"]
        assert data["plugins"][0]["ungrantable"] == []

    def test_it_survives_a_broken_plugin(self, monkeypatch):
        """读不出来不许把整屏带下去 —— 空清单 ＋ 日志出声。"""
        from plobi_cli import plugins

        try:
            from starlette.testclient import TestClient
        except ImportError:
            import pytest

            pytest.skip("fastapi/starlette not installed")

        import plobi_state
        from plobi_constants import get_plobi_home
        from plobi_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

        monkeypatch.setattr(plobi_state, "DEFAULT_DB_PATH", get_plobi_home() / "state.db")
        client = TestClient(app)
        client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

        def _boom():
            raise RuntimeError("plugin exploded")

        monkeypatch.setattr(plugins, "get_plugin_capabilities", _boom)

        resp = client.get("/api/plugin-capabilities")

        assert resp.status_code == 200
        assert resp.json() == {"plugins": []}
