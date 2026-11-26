"""A plugin can declare what its toolset looks like on the capabilities screen.

``WP-PLUGIN-TOOLSET-CARDS`` — a plugin's toolset used to reach the screen as
an auto-generated name ("🔌 My Thing") and an **empty** panel: the row had no
shape, because the manifest had nowhere to say "this is what my toolset is".
``plugin.yaml`` now carries a ``toolsets:`` declaration and the screen
renders it.

These tests pin relations, not today's wording:

* a declared card is what the capability list shows — its own label, not the
  auto-generated one;
* the config endpoint serves that card as the toolset's row, with the
  credential fields and their current state;
* a plugin that declares nothing keeps the old behaviour (no regression).
"""

import types

import pytest


def _manifest(**kwargs):
    from plobi_cli.plugins import PluginManifest

    return PluginManifest(name="my-audio", **kwargs)


def _fake_manager(toolsets=(), tool_names=(), plugin_tools=()):
    """A stand-in for the plugin manager: declared cards and registered tools."""
    plugin = types.SimpleNamespace(
        manifest=_manifest(toolsets=list(toolsets)),
        tools_registered=list(plugin_tools),
    )
    return types.SimpleNamespace(
        _plugins={"my-audio": plugin},
        _plugin_tool_names=set(tool_names),
    )


@pytest.fixture
def declared_card():
    return {
        "key": "my_audio",
        "label": "My Audio",
        "description": "Audio backends for agents",
        "env_vars": [
            {"key": "MY_AUDIO_KEY", "prompt": "My Audio API key"},
        ],
    }


def test_manifest_keeps_the_declared_toolsets(tmp_path):
    from plobi_cli.plugins import get_plugin_manager

    plugin_dir = tmp_path / "my-audio"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: my-audio\n"
        "kind: standalone\n"
        "toolsets:\n"
        "  - key: my_audio\n"
        "    label: My Audio\n"
        "    description: Audio backends for agents\n"
        "    env_vars:\n"
        "      - key: MY_AUDIO_KEY\n"
        "        prompt: My Audio API key\n",
        encoding="utf-8",
    )

    manifest = get_plugin_manager()._parse_manifest(
        plugin_dir / "plugin.yaml", plugin_dir, "user", ""
    )

    assert manifest is not None
    assert manifest.toolsets, "the manifest dropped the declared toolset card"
    card = manifest.toolsets[0]
    assert card["key"] == "my_audio"
    assert card["label"] == "My Audio"
    assert card["env_vars"][0]["key"] == "MY_AUDIO_KEY"


def test_the_declared_card_is_collected_by_toolset_key(monkeypatch, declared_card):
    from plobi_cli import plugins

    monkeypatch.setattr(
        plugins, "get_plugin_manager", lambda: _fake_manager(toolsets=[declared_card])
    )

    cards = plugins.get_plugin_toolset_cards()

    assert "my_audio" in cards
    assert cards["my_audio"]["label"] == "My Audio"


def test_the_capability_list_shows_the_declared_label(monkeypatch, declared_card):
    from plobi_cli import plugins
    from tools.registry import registry

    entry = types.SimpleNamespace(name="my_audio_speak", toolset="my_audio")
    monkeypatch.setattr(registry, "get_entry", lambda name: entry)
    monkeypatch.setattr(
        plugins,
        "get_plugin_manager",
        lambda: _fake_manager(
            toolsets=[declared_card],
            tool_names=["my_audio_speak"],
            plugin_tools=["my_audio_speak"],
        ),
    )

    toolsets = dict((key, (label, desc)) for key, label, desc in plugins.get_plugin_toolsets())

    assert toolsets["my_audio"][0] == "My Audio"
    assert toolsets["my_audio"][1] == "Audio backends for agents"


def test_a_plugin_that_declares_nothing_keeps_the_old_name(monkeypatch):
    from plobi_cli import plugins
    from tools.registry import registry

    entry = types.SimpleNamespace(name="my_thing_tool", toolset="my_thing")
    monkeypatch.setattr(registry, "get_entry", lambda name: entry)
    monkeypatch.setattr(
        plugins,
        "get_plugin_manager",
        lambda: _fake_manager(tool_names=["my_thing_tool"], plugin_tools=["my_thing_tool"]),
    )

    toolsets = dict((key, (label, desc)) for key, label, desc in plugins.get_plugin_toolsets())

    assert "my_thing" in toolsets
    assert toolsets["my_thing"][0], "a plugin with no declaration still gets a name"


class TestToolsetConfigEndpoint:
    """``GET /api/tools/toolsets/{name}/config`` serves a declared card."""

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

    def test_the_declared_card_is_served_as_the_toolsets_row(self, monkeypatch, declared_card):
        from plobi_cli import plugins, tools_config

        monkeypatch.setattr(
            plugins, "get_plugin_toolset_cards", lambda: {"my_audio": declared_card}
        )
        monkeypatch.setattr(
            tools_config,
            "_get_effective_configurable_toolsets",
            lambda: [("my_audio", "My Audio", "Audio backends for agents")],
        )

        resp = self.client.get("/api/tools/toolsets/my_audio/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_category"] is True
        assert len(data["providers"]) == 1
        row = data["providers"][0]
        assert row["name"] == "My Audio"
        assert row["tag"] == "Audio backends for agents"
        assert row["env_vars"][0]["key"] == "MY_AUDIO_KEY"
        assert "is_set" in row["env_vars"][0]
