"""The settings screen's provider dropdown must offer the providers the backend has.

``WP-SCREEN-OPTIONS-REGISTRY`` — the screen used to read its own hardcoded
table (``apps/desktop/src/app/settings/constants.ts``), so a provider the
backend knows about but the table never heard of — a plugin-registered TTS
backend, a command provider the user wired up — was installed and *not
selectable* ("插件装上了、屏上却选不到").  The option list now comes from the
registries.

These tests pin **relationships**, not a snapshot of today's provider count:
whatever the built-ins are, they must all be selectable; whatever a plugin
registers must show up with its own name; whatever the user declared must
show up too.  A count assertion would fail the day a provider is added
without telling anyone anything true.
"""

import pytest


@pytest.fixture(autouse=True)
def _clean_registries():
    """Leave both provider registries as we found them."""
    from agent import tts_registry, transcription_registry

    tts_registry._reset_for_tests()
    transcription_registry._reset_for_tests()
    yield
    tts_registry._reset_for_tests()
    transcription_registry._reset_for_tests()


class _FakeTTSProvider:
    """A plugin-style TTS provider: the registry wants the real ABC."""

    def __new__(cls, name: str, display_name: str):
        from agent.tts_provider import TTSProvider

        class _Impl(TTSProvider):
            @property
            def name(self) -> str:
                return name

            @property
            def display_name(self) -> str:
                return display_name

            def synthesize(self, text, output_path, **kwargs) -> str:
                return output_path

        return _Impl()


class _FakeSTTProvider:
    """A plugin-style transcription provider."""

    def __new__(cls, name: str, display_name: str):
        from agent.transcription_provider import TranscriptionProvider

        class _Impl(TranscriptionProvider):
            @property
            def name(self) -> str:
                return name

            @property
            def display_name(self) -> str:
                return display_name

            def transcribe(self, audio_path, **kwargs) -> str:
                return ""

        return _Impl()


def _options(key: str) -> list:
    from plobi_cli.tools_config import provider_config_options

    return provider_config_options()[key]


def _values(options: list) -> list:
    return [o["value"] for o in options]


def _entry(options: list, value: str) -> dict:
    for option in options:
        if option["value"] == value:
            return option
    raise AssertionError(f"{value!r} is not among the options: {_values(options)}")


# ---------------------------------------------------------------------------
# built-ins
# ---------------------------------------------------------------------------


def test_every_builtin_tts_provider_is_selectable():
    from tools.tts_tool import BUILTIN_TTS_PROVIDERS

    values = _values(_options("tts.provider"))
    missing = [name for name in BUILTIN_TTS_PROVIDERS if name not in values]
    assert missing == [], f"built-in TTS providers that cannot be picked: {missing}"


def test_every_builtin_stt_provider_is_selectable():
    from tools.transcription_tools import BUILTIN_STT_PROVIDERS

    values = _values(_options("stt.provider"))
    missing = [name for name in BUILTIN_STT_PROVIDERS if name not in values]
    assert missing == [], f"built-in STT providers that cannot be picked: {missing}"


def test_builtin_tts_providers_carry_a_human_label():
    """The dropdown shows a name, not a slug — the picker table already spells them out."""
    from plobi_cli.tools_config import TOOL_CATEGORIES

    options = _options("tts.provider")
    labelled = {o["value"]: o.get("label") for o in options}
    named = 0
    for row in (TOOL_CATEGORIES.get("tts") or {}).get("providers") or []:
        value = row.get("tts_provider")
        name = row.get("name")
        if not (isinstance(value, str) and value and isinstance(name, str) and name):
            continue
        if row.get("managed_nous_feature") or row.get("requires_nous_auth"):
            # A billing variant of a provider, not the provider itself — it
            # never donates the name (otherwise ``openai`` would be "Nous").
            continue
        assert labelled.get(value) == name, f"{value} is labelled {labelled.get(value)!r}"
        named += 1
    assert named, "the picker table spells out no built-in TTS provider"


# ---------------------------------------------------------------------------
# plugin-registered providers
# ---------------------------------------------------------------------------


def test_plugin_registered_tts_provider_is_selectable():
    from agent.tts_registry import register_provider

    register_provider(_FakeTTSProvider("fake-tts", "Fake TTS"))

    option = _entry(_options("tts.provider"), "fake-tts")
    assert option["source"] == "plugin"
    assert option.get("label") == "Fake TTS"


def test_plugin_registered_stt_provider_is_selectable():
    from agent.transcription_registry import register_provider

    register_provider(_FakeSTTProvider("fake-stt", "Fake STT"))

    option = _entry(_options("stt.provider"), "fake-stt")
    assert option["source"] == "plugin"
    assert option.get("label") == "Fake STT"


def test_a_provider_the_backend_does_not_have_is_not_offered():
    """The other half of the relationship: a name nobody registered stays unselectable."""
    values = _values(_options("tts.provider"))
    assert "definitely-not-a-provider" not in values


# ---------------------------------------------------------------------------
# command providers the user declared
# ---------------------------------------------------------------------------


def test_declared_command_tts_provider_is_selectable():
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    (home / "config.yaml").write_text(
        "tts:\n  providers:\n    my-cli-tts:\n      type: command\n      command: echo hi\n",
        encoding="utf-8",
    )

    option = _entry(_options("tts.provider"), "my-cli-tts")
    assert option["source"] == "command"


def test_declared_command_stt_provider_is_selectable():
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    (home / "config.yaml").write_text(
        "stt:\n  providers:\n    my-cli-stt:\n      type: command\n      command: echo hi\n",
        encoding="utf-8",
    )

    option = _entry(_options("stt.provider"), "my-cli-stt")
    assert option["source"] == "command"


# ---------------------------------------------------------------------------
# the endpoint the screen reads
# ---------------------------------------------------------------------------


class TestSchemaEndpoint:
    """``GET /api/config/schema`` carries the live options for provider keys."""

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

    def test_provider_keys_carry_registry_options(self):
        from tools.tts_tool import BUILTIN_TTS_PROVIDERS

        resp = self.client.get("/api/config/schema")
        assert resp.status_code == 200
        fields = resp.json()["fields"]

        entry = fields["tts.provider"]
        assert entry["options_source"] == "registry"
        missing = [n for n in BUILTIN_TTS_PROVIDERS if n not in entry["options"]]
        assert missing == []
        assert entry["option_labels"], "a registry-backed key must carry its labels"

    def test_keys_without_a_registry_keep_their_own_options(self):
        """Only registry-backed keys are authoritative — the rest stay the screen's business."""
        resp = self.client.get("/api/config/schema")
        fields = resp.json()["fields"]
        assert "options_source" not in fields["approvals.mode"]
