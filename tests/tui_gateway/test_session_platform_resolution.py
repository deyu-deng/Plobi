"""Platform/source tagging for the desktop chat surface.

The desktop app's chat panel uses ``plobi serve`` (the ``tui_gateway``
backend), so every chat session historically got ``platform="tui"`` stamped
on it — even though the user is in a graphical chat surface, not a
terminal. That mis-tag is why the agent suggested TUI-only slash commands
(like ``/reload-mcp``) to desktop chat users.

These tests pin the env-var matrix that resolves the session platform at
``tui_gateway`` session-creation time:

  PLOBI_DESKTOP=1, PLOBI_DESKTOP_TERMINAL unset  -> platform="desktop"
  PLOBI_DESKTOP=1, PLOBI_DESKTOP_TERMINAL=1     -> platform="tui"  (embedded pane)
  neither set                                      -> platform="tui"  (standalone)

The resolver helper is import-safe (no heavy module side effects) so it
can be unit-tested without spinning up the full gateway.
"""

import pytest

from plobi_cli.config import load_config as _load_cfg_for_test
from plobi_cli.config import save_config as _save_cfg_for_test
from plobi_cli.tools_config import (
    PLATFORMS,
    _get_platform_tools,
    _save_platform_tools,
)


def _reload_resolver():
    # Plain import — every resolver under test reads the env at CALL time, so
    # no reload is needed. importlib.reload(tui_gateway.server) would
    # re-register the module's atexit hooks (thread-pool shutdown +
    # _shutdown_sessions) on every test; duplicated hooks race the stderr
    # buffer at interpreter shutdown (Fatal Python error:
    # _enter_buffered_busy) — same flake class as PR #34217. Name kept for
    # the existing call sites.
    import tui_gateway.server as _srv
    return _srv


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("PLOBI_DESKTOP", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP_TERMINAL", raising=False)
    return monkeypatch


class TestResolveSessionPlatform:
    def test_standalone_tui_neither_env_set(self, clean_env):
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "tui"

    def test_desktop_chat_backend_gets_desktop_tag(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "desktop"

    def test_desktop_embedded_terminal_pane_stays_tui(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        clean_env.setenv("PLOBI_DESKTOP_TERMINAL", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "tui"

    def test_desktop_terminal_alone_means_standalone_tui(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP_TERMINAL", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "tui"

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "Yes", "ON"])
    def test_truthy_variants_recognized(self, clean_env, val):
        clean_env.setenv("PLOBI_DESKTOP", val)
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "desktop"

    @pytest.mark.parametrize("val", ["0", "false", "", "no", "off", "False"])
    def test_falsy_variants_fall_back_to_tui(self, clean_env, val):
        clean_env.setenv("PLOBI_DESKTOP", val)
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "tui"

    def test_embedded_terminal_overrides_desktop_when_both_set(self, clean_env):
        """The terminal-pane qualifier must short-circuit the desktop-backend
        marker. An embedded TUI is a TUI, not a desktop chat surface."""
        clean_env.setenv("PLOBI_DESKTOP", "1")
        clean_env.setenv("PLOBI_DESKTOP_TERMINAL", "true")
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "tui"


class TestResolveSessionSource:
    def test_explicit_source_param_wins(self, clean_env):
        _srv = _reload_resolver()
        assert _srv._resolve_session_source("telegram") == "telegram"

    def test_explicit_empty_source_falls_back_to_env(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_session_source("") == "desktop"

    def test_explicit_none_source_falls_back_to_env(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_session_source(None) == "desktop"

    def test_no_env_no_param_defaults_to_tui(self, clean_env):
        _srv = _reload_resolver()
        assert _srv._resolve_session_source(None) == "tui"

    def test_embedded_terminal_default_is_tui(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        clean_env.setenv("PLOBI_DESKTOP_TERMINAL", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_session_source(None) == "tui"

    def test_explicit_source_param_resists_env_drift(self, clean_env):
        """A caller that explicitly passes source="cli" must not be silently
        rewritten to "desktop" by env vars — the resolver only fills in the
        default when one is missing."""
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_session_source("cli") == "cli"


class TestResolveAgentPlatform:
    def test_explicit_desktop_source_drives_agent_platform_without_env(self, clean_env):
        _srv = _reload_resolver()
        assert _srv._resolve_agent_platform("desktop") == "desktop"

    def test_missing_source_falls_back_to_env_resolved_platform(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_agent_platform(None) == "desktop"

    def test_explicit_tui_source_keeps_embedded_terminal_as_tui(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._resolve_agent_platform("tui") == "tui"


class TestSessionSourceFallback:
    def test_session_source_uses_existing_session_value(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._session_source({"source": "telegram"}) == "telegram"

    def test_session_source_defaults_to_desktop_under_desktop_backend(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        _srv = _reload_resolver()
        assert _srv._session_source({}) == "desktop"
        assert _srv._session_source(None) == "desktop"

    def test_session_source_defaults_to_tui_for_embedded_terminal(self, clean_env):
        clean_env.setenv("PLOBI_DESKTOP", "1")
        clean_env.setenv("PLOBI_DESKTOP_TERMINAL", "1")
        _srv = _reload_resolver()
        assert _srv._session_source({}) == "tui"
        assert _srv._session_source(None) == "tui"


# ---------------------------------------------------------------------------
# Platform IDENTITY for toolsets: the ``desktop`` stamp is a presentation tag
# (session source + system-prompt hints + focus-mode posture). The list of
# toolsets the desktop chat agent actually gets is resolved through the same
# ``platform_toolsets`` key that the write side owns — the desktop Toolsets
# panel (``PUT /api/tools/toolsets/{name}`` -> ``_save_platform_tools(cfg,
# "cli", ...)``) and ``plobi tools enable`` (default platform ``cli``).
#
# This is the guard for the bug class that reads as "the secretary has no
# tools": if the read side ever keys off the stamped platform while the write
# side keeps saving elsewhere (or vice versa), every tick in the GUI lands on a
# platform the session never reads, and the user is told they can have any tool
# while the agent gets none. Assert the two sides agree — never a frozen list
# of toolset names.
# ---------------------------------------------------------------------------

# Two configurable toolsets used as distinguishable markers: whichever side of
# the read/write split a name is put on tells us which key was honored.
_ENABLED_MARKER = "tts"
_ABSENT_MARKER = "vision"


@pytest.fixture
def desktop_home(tmp_path, monkeypatch):
    """A temp PLOBI_HOME whose ``cli`` and ``desktop`` toolset lists differ."""
    home = tmp_path / ".plobi"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.delenv("PLOBI_TUI_TOOLSETS", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP_TERMINAL", raising=False)
    monkeypatch.setenv("PLOBI_DESKTOP", "1")
    (home / "config.yaml").write_text(
        "model: anthropic/claude-sonnet-4\n"
        "platform_toolsets:\n"
        "  cli:\n"
        "  - file\n"
        f"  - {_ENABLED_MARKER}\n"
        "  desktop:\n"
        "  - file\n"
        f"  - {_ABSENT_MARKER}\n"
    )
    return home


class TestDesktopToolsetPlatformIdentity:
    def test_desktop_session_honors_the_key_the_panel_writes(self, desktop_home):
        """The stamped platform may be ``desktop``; the toolsets it resolves
        must be the ones the write side saves. Enabling a toolset for the
        desktop-facing platform therefore has to reach the desktop session, and
        a toolset that only the phantom ``platform_toolsets.desktop`` list
        names must not."""
        _srv = _reload_resolver()
        assert _srv._resolve_session_platform() == "desktop"

        resolved = _srv._load_enabled_toolsets()
        assert resolved is not None, "desktop session resolved no toolsets at all"
        assert _ENABLED_MARKER in resolved, (
            "a toolset the desktop write side enabled is missing from the "
            f"desktop session's resolved toolsets: {sorted(resolved)}"
        )
        assert _ABSENT_MARKER not in resolved

    def test_read_key_is_addressable_by_the_write_path(self, desktop_home, monkeypatch):
        """Derive the platform key the desktop read path actually uses (do not
        hardcode it), then require the write path to accept that same key and
        round-trip it. A key the panel cannot name means every GUI toggle is
        written to a platform the session never reads."""
        import plobi_cli.tools_config as tools_config

        captured: list[str] = []
        real = tools_config._get_platform_tools

        def spy(config, platform, **kwargs):
            captured.append(platform)
            return real(config, platform, **kwargs)

        monkeypatch.setattr(tools_config, "_get_platform_tools", spy)
        _reload_resolver()._load_enabled_toolsets()
        assert captured, "desktop toolset resolution never consulted a platform key"
        read_key = captured[-1]

        cfg = _load_cfg_for_test()
        written = set(_get_platform_tools(cfg, read_key)) | {_ABSENT_MARKER}
        _save_platform_tools(cfg, read_key, written)
        _save_cfg_for_test(cfg)
        assert (cfg.get("platform_toolsets") or {}).get(read_key), (
            f"the write path refused to save platform_toolsets.{read_key} — the "
            "desktop session reads a key the product cannot address"
        )

        resolved = _reload_resolver()._load_enabled_toolsets() or []
        assert _ABSENT_MARKER in resolved, (
            "enabling a toolset on the desktop session's own platform key did not "
            "reach the desktop session"
        )

    def test_platform_fallback_composites_resolve_to_tools(self):
        """A platform with no saved list falls back to a composite — the
        ``PLATFORMS`` default, or the derived ``plobi-<platform>`` name the
        plugin branch builds. Either way it must enumerate tools: a composite
        that is absent from ``TOOLSETS`` expands to nothing and silently leaves
        that surface toolless, which is what a 'no tools at all' report looks
        like from the user's side."""
        from toolsets import TOOLSETS, resolve_toolset

        for platform, info in PLATFORMS.items():
            composite = info["default_toolset"]
            assert composite in TOOLSETS, (
                f"platform {platform!r} falls back to {composite!r}, which is not "
                "a defined toolset — an unconfigured session gets no tools"
            )
            assert resolve_toolset(composite), (
                f"platform fallback composite {composite!r} resolves to no tools"
            )
