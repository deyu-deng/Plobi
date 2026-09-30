"""Tests for the DuckDuckGo (ddgs) web search provider.

Covers:
- DDGSWebSearchProvider.is_available() — reflects package importability
- DDGSWebSearchProvider.search() — happy path, missing package, runtime error
- Result normalization (title, url, description, position)
- _is_backend_available("ddgs") / _get_backend() integration
- web_extract returns a search-only error when ddgs is active
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from tests.tools.conftest import register_all_web_providers


def _install_fake_ddgs(monkeypatch, *, text_results=None, text_raises=None, text_sleep=None):
    """Install a stub ``ddgs`` module in sys.modules for the duration of a test.

    ``text_results``: iterable of dicts to yield from DDGS().text(...).
    ``text_raises``: if set, DDGS().text raises this exception instead.
    ``text_sleep``: if set, DDGS().text blocks for this many seconds before
        yielding — simulates a hung/slow search for the timeout test.
    """
    import time as _time

    fake = types.ModuleType("ddgs")

    class _FakeDDGS:
        def __init__(self, **kwargs):
            # Accept timeout= (and any other constructor kwargs) — the provider
            # now passes DDGS(timeout=10).
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_a):
            return False
        def text(self, query, max_results=5):
            if text_sleep is not None:
                _time.sleep(text_sleep)
            if text_raises is not None:
                raise text_raises
            for hit in (text_results or []):
                yield hit

    fake.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake)
    return fake


# ---------------------------------------------------------------------------
# DDGSWebSearchProvider unit tests
# ---------------------------------------------------------------------------


class TestDDGSProviderIsConfigured:
    def test_configured_when_package_importable(self, monkeypatch):
        _install_fake_ddgs(monkeypatch)
        # Drop any cached ``plugins.web.ddgs.provider`` so is_configured re-imports ddgs fresh
        monkeypatch.delitem(sys.modules, "plugins.web.ddgs.provider", raising=False)
        from plugins.web.ddgs.provider import DDGSWebSearchProvider
        assert DDGSWebSearchProvider().is_available() is True

    def test_not_configured_when_package_missing(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.delitem(sys.modules, "plugins.web.ddgs.provider", raising=False)
        # Block the import so ``import ddgs`` raises ImportError even if the package is actually installed
        import builtins
        orig_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "ddgs":
                raise ImportError("blocked for test")
            return orig_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        from plugins.web.ddgs.provider import DDGSWebSearchProvider
        assert DDGSWebSearchProvider().is_available() is False

    def test_provider_name(self):
        from plugins.web.ddgs.provider import DDGSWebSearchProvider
        assert DDGSWebSearchProvider().name == "ddgs"

    def test_implements_web_search_provider(self):
        from agent.web_search_provider import WebSearchProvider
        from plugins.web.ddgs.provider import DDGSWebSearchProvider
        assert issubclass(DDGSWebSearchProvider, WebSearchProvider)


class TestDDGSProviderSearch:
    def test_happy_path_normalizes_results(self, monkeypatch):
        _install_fake_ddgs(monkeypatch, text_results=[
            {"title": "A", "href": "https://a.example.com", "body": "desc A"},
            {"title": "B", "href": "https://b.example.com", "body": "desc B"},
            {"title": "C", "href": "https://c.example.com", "body": "desc C"},
        ])
        from plugins.web.ddgs.provider import DDGSWebSearchProvider

        result = DDGSWebSearchProvider().search("q", limit=5)

        assert result["success"] is True
        web = result["data"]["web"]
        assert len(web) == 3
        assert web[0] == {"title": "A", "url": "https://a.example.com", "description": "desc A", "position": 1}
        assert web[2]["position"] == 3

    def test_accepts_url_key_as_fallback_for_href(self, monkeypatch):
        _install_fake_ddgs(monkeypatch, text_results=[
            {"title": "A", "url": "https://a.example.com", "body": "desc A"},
        ])
        from plugins.web.ddgs.provider import DDGSWebSearchProvider

        result = DDGSWebSearchProvider().search("q", limit=5)

        assert result["success"] is True
        assert result["data"]["web"][0]["url"] == "https://a.example.com"

    def test_limit_is_respected(self, monkeypatch):
        _install_fake_ddgs(monkeypatch, text_results=[
            {"title": f"R{i}", "href": f"https://r{i}.example.com", "body": ""}
            for i in range(10)
        ])
        from plugins.web.ddgs.provider import DDGSWebSearchProvider

        result = DDGSWebSearchProvider().search("q", limit=3)

        assert result["success"] is True
        assert len(result["data"]["web"]) == 3

    def test_missing_package_returns_failure(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.delitem(sys.modules, "plugins.web.ddgs.provider", raising=False)
        import builtins
        orig_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "ddgs":
                raise ImportError("blocked for test")
            return orig_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        from plugins.web.ddgs.provider import DDGSWebSearchProvider

        result = DDGSWebSearchProvider().search("q", limit=5)
        assert result["success"] is False
        assert "ddgs" in result["error"].lower()

    def test_runtime_error_returns_failure(self, monkeypatch):
        _install_fake_ddgs(monkeypatch, text_raises=RuntimeError("rate limited 202"))
        from plugins.web.ddgs.provider import DDGSWebSearchProvider

        result = DDGSWebSearchProvider().search("q", limit=5)
        assert result["success"] is False
        assert "rate limited" in result["error"] or "failed" in result["error"].lower()

    def test_empty_results(self, monkeypatch):
        _install_fake_ddgs(monkeypatch, text_results=[])
        from plugins.web.ddgs.provider import DDGSWebSearchProvider

        result = DDGSWebSearchProvider().search("nothing", limit=5)
        assert result["success"] is True
        assert result["data"]["web"] == []

    def test_hung_search_times_out_and_returns_failure(self, monkeypatch):
        """#36776: a ddgs call that never returns must be bounded by the
        wall-clock timeout and surface a failure instead of hanging the
        shared agent loop. We patch the blocking helper to wait on an Event
        (released in finally so no worker thread leaks past the test) and
        shrink the timeout; search() must return success=False promptly."""
        import threading
        import time

        # ddgs must import-probe True for search() to proceed.
        _install_fake_ddgs(monkeypatch)
        monkeypatch.delitem(sys.modules, "plugins.web.ddgs.provider", raising=False)
        import plugins.web.ddgs.provider as _prov

        release = threading.Event()

        def _blocking_search(query, safe_limit):
            release.wait(timeout=10)  # bounded so the worker can never truly leak
            return []

        monkeypatch.setattr(_prov, "_run_ddgs_search", _blocking_search, raising=True)
        monkeypatch.setattr(_prov, "_SEARCH_TIMEOUT_SECS", 0.3, raising=True)

        try:
            start = time.monotonic()
            result = _prov.DDGSWebSearchProvider().search("hangs forever", limit=5)
            elapsed = time.monotonic() - start

            assert result["success"] is False
            assert "timed out" in result["error"].lower()
            # Returned well before the worker's 10s wait — proves the cap fired.
            assert elapsed < 3.0, f"search did not return promptly ({elapsed:.1f}s)"
        finally:
            release.set()  # let the orphaned worker finish immediately

    def test_fast_search_not_affected_by_timeout_wrapper(self, monkeypatch):
        """Happy-path guard: the timeout wrapper must not break a normal,
        fast search — results flow through unchanged."""
        _install_fake_ddgs(
            monkeypatch,
            text_results=[{"title": "T", "href": "https://e.com", "body": "B"}],
        )
        from plugins.web.ddgs.provider import DDGSWebSearchProvider

        result = DDGSWebSearchProvider().search("q", limit=5)
        assert result["success"] is True
        assert result["data"]["web"][0]["url"] == "https://e.com"
        assert result["data"]["web"][0]["title"] == "T"


# ---------------------------------------------------------------------------
# Integration: _is_backend_available / _get_backend / check_web_api_key
# ---------------------------------------------------------------------------


class TestDDGSBackendWiring:
    def test_is_backend_available_true_when_package_importable(self, monkeypatch):
        from tools import web_tools
        monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: True)
        assert web_tools._is_backend_available("ddgs") is True

    def test_is_backend_available_false_when_package_missing(self, monkeypatch):
        from tools import web_tools
        monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: False)
        assert web_tools._is_backend_available("ddgs") is False

    def test_configured_backend_accepted(self, monkeypatch):
        from tools import web_tools
        monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"backend": "ddgs"})
        monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: True)
        assert web_tools._get_backend() == "ddgs"

    def test_ddgs_trails_paid_providers_in_auto_detect(self, monkeypatch):
        """Exa (priority) should win over ddgs in auto-detect."""
        from tools import web_tools
        monkeypatch.setattr(web_tools, "_load_web_config", lambda: {})
        for key in ("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL", "PARALLEL_API_KEY",
                    "TAVILY_API_KEY", "SEARXNG_URL", "BRAVE_SEARCH_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("EXA_API_KEY", "exa-key")
        monkeypatch.setattr(web_tools, "_is_tool_gateway_ready", lambda: False)
        monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: True)
        assert web_tools._get_backend() == "exa"

    def test_auto_detect_picks_ddgs_as_last_resort(self, monkeypatch):
        from tools import web_tools
        monkeypatch.setattr(web_tools, "_load_web_config", lambda: {})
        for key in ("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL", "PARALLEL_API_KEY",
                    "TAVILY_API_KEY", "EXA_API_KEY", "SEARXNG_URL", "BRAVE_SEARCH_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(web_tools, "_is_tool_gateway_ready", lambda: False)
        monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: True)
        assert web_tools._get_backend() == "ddgs"

    def test_check_web_api_key_true_when_ddgs_configured(self, monkeypatch):
        from tools import web_tools
        monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"backend": "ddgs"})
        monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: True)
        assert web_tools.check_web_api_key() is True


# ---------------------------------------------------------------------------
# ddgs is lazy-installable: probe stays cheap, ensure() does the installing
#
# tools.web_tools._ensure_ddgs_package() is the ddgs counterpart to the
# ensure() calls in plugins/web/{exa,firecrawl,parallel}/provider.py. These
# tests mock tools.lazy_deps.ensure / _venv_pip_install — nothing here ever
# touches the network or pip.
# ---------------------------------------------------------------------------


class TestDDGSLazyInstall:
    def test_probe_stays_a_pure_probe(self, monkeypatch):
        """_is_backend_available / check_web_api_key must not pip install.

        The probe runs at tool-registration time and on every ``plobi tools``
        repaint; if it installed, merely listing the providers would shell out
        to pip. Installation belongs on the dispatch path only.
        """
        from tools import web_tools

        calls: list[str] = []
        import tools.lazy_deps as ld
        monkeypatch.setattr(
            ld, "ensure", lambda *a, **kw: calls.append("ensure")
        )
        monkeypatch.delitem(sys.modules, "ddgs", raising=False)

        assert web_tools._ddgs_package_importable() in (True, False)
        assert calls == [], "the availability probe must never install anything"

    def test_ensure_installs_through_the_search_ddgs_feature(self, monkeypatch):
        """One installer path, and it is the repo's: lazy_deps.ensure."""
        from tools import web_tools

        seen: list[tuple] = []
        import tools.lazy_deps as ld

        def fake_ensure(feature, **kwargs):
            seen.append((feature, kwargs))
            _install_fake_ddgs(monkeypatch)  # the install "succeeds"

        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.setattr(ld, "ensure", fake_ensure)
        monkeypatch.setattr(
            web_tools, "_ddgs_package_importable",
            lambda: "ddgs" in sys.modules,
        )

        assert web_tools._ensure_ddgs_package() is True
        assert seen == [("search.ddgs", {"prompt": False})]

    def test_ensure_skips_the_installer_when_already_present(self, monkeypatch):
        """The paid-SDK shape: cached/imported → no ensure round-trip."""
        from tools import web_tools

        calls: list[str] = []
        import tools.lazy_deps as ld

        _install_fake_ddgs(monkeypatch)
        monkeypatch.setattr(ld, "ensure", lambda *a, **kw: calls.append("ensure"))
        monkeypatch.setattr(
            web_tools, "_ddgs_package_importable",
            lambda: "ddgs" in sys.modules,
        )

        assert web_tools._ensure_ddgs_package() is True
        assert calls == []

    def test_feature_unavailable_is_reported_not_raised(self, monkeypatch):
        """security.allow_lazy_installs=false → False, never an exception.

        A user who opted out of runtime installs still gets the provider's own
        single clear message, not a traceback out of the dispatch path.
        """
        from tools import web_tools
        import tools.lazy_deps as ld

        def refuse(feature, **kwargs):
            raise ld.FeatureUnavailable(
                feature, ("ddgs==1.2.3",),
                "lazy installs disabled (security.allow_lazy_installs=false)",
            )

        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.setattr(ld, "ensure", refuse)
        monkeypatch.setattr(
            web_tools, "_ddgs_package_importable",
            lambda: "ddgs" in sys.modules,
        )

        assert web_tools._ensure_ddgs_package() is False

    def test_disabled_lazy_installs_never_reach_pip(self, monkeypatch):
        """End-to-end through the real ensure(): opt-out short-circuits pip.

        Only the subprocess boundary and the config flag are mocked, so this
        exercises the actual gating order inside lazy_deps — allowlist lookup,
        missing-spec check, then ``_allow_lazy_installs`` — instead of just
        asserting that a mock was called.
        """
        from tools import web_tools
        import tools.lazy_deps as ld

        pip_calls: list[tuple] = []
        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.setattr(ld, "_is_satisfied", lambda spec: False)
        monkeypatch.setattr(ld, "_allow_lazy_installs", lambda: False)
        monkeypatch.setattr(
            ld, "_venv_pip_install",
            lambda specs, **kw: pip_calls.append(tuple(specs)) or ld._InstallResult(True, "", ""),
        )
        monkeypatch.setattr(
            web_tools, "_ddgs_package_importable",
            lambda: "ddgs" in sys.modules,
        )

        assert web_tools._ensure_ddgs_package() is False
        assert pip_calls == [], "a disabled lazy-install policy must never shell out"

    def test_install_runs_before_the_package_is_needed(self, monkeypatch):
        """The ordering fix: a fresh install lights the tool up in-process.

        Without ensure() on the dispatch path, ddgs reports "not installed"
        forever — the probe that gates the tool reads the very package the
        installer changes. Here the package is genuinely absent until the
        installer runs, and the probe reports it present immediately after.
        """
        from tools import web_tools
        import tools.lazy_deps as ld

        state = {"installed": False}
        order: list[str] = []

        def fake_ensure(feature, **kwargs):
            order.append(f"ensure:{feature}")
            assert not state["installed"], "installer ran after the install"
            _install_fake_ddgs(monkeypatch, text_results=[
                {"title": "Fresh", "href": "https://fresh.example.com", "body": "b"},
            ])
            state["installed"] = True

        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.setattr(
            web_tools, "_ddgs_package_importable", lambda: state["installed"]
        )
        monkeypatch.setattr(ld, "ensure", fake_ensure)

        assert web_tools._ensure_ddgs_package() is True
        assert order == ["ensure:search.ddgs"]
        # Gate reopens in the same process — no restart required.
        assert web_tools._ddgs_package_importable() is True

    def test_disabled_installer_still_leaves_probe_false(self, monkeypatch):
        """A failed install must not flip the gate optimistically."""
        from tools import web_tools
        import tools.lazy_deps as ld

        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.setattr(ld, "ensure", lambda feature, **kw: None)
        monkeypatch.setattr(
            web_tools, "_ddgs_package_importable", lambda: "ddgs" in sys.modules
        )

        assert web_tools._ensure_ddgs_package() is False


# ---------------------------------------------------------------------------
# ddgs is search-only: web_extract returns a clear error
# ---------------------------------------------------------------------------


class TestDDGSSearchOnlyErrors:
    _register_providers = staticmethod(register_all_web_providers)

    @pytest.fixture(autouse=True)
    def _populate_web_registry(self):
        self._register_providers()
        yield
        from agent.web_search_registry import _reset_for_tests
        _reset_for_tests()

    def test_web_extract_returns_search_only_error(self, monkeypatch):
        import asyncio
        from tools import web_tools

        monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"backend": "ddgs"})
        monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: True)
        monkeypatch.setattr(web_tools, "_is_tool_gateway_ready", lambda: False)
        async def _allow_ssrf(_url: str) -> bool:
            return True

        monkeypatch.setattr(web_tools, "async_is_safe_url", _allow_ssrf)
        monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False, raising=False)

        result_str = asyncio.get_event_loop().run_until_complete(
            web_tools.web_extract_tool(["https://example.com"])
        )
        result = json.loads(result_str)
        assert result["success"] is False
        assert "search-only" in result["error"].lower()
        assert "duckduckgo" in result["error"].lower() or "ddgs" in result["error"].lower()


# ---------------------------------------------------------------------------
# Dispatch integration: web_search_tool installs ddgs instead of dead-ending
# ---------------------------------------------------------------------------


class TestDDGSDispatchLazyInstall:
    @pytest.fixture(autouse=True)
    def _populate_web_registry(self):
        register_all_web_providers()
        yield
        from agent.web_search_registry import _reset_for_tests
        _reset_for_tests()

    def _wire(self, monkeypatch):
        """Return the state log after wiring a missing ddgs + fake installer."""
        from tools import web_tools
        import tools.lazy_deps as ld

        state = {"installed": False}
        order: list[str] = []

        def fake_ensure(feature, **kwargs):
            order.append(f"ensure:{feature}")
            _install_fake_ddgs(monkeypatch, text_results=[
                {"title": "Installed", "href": "https://ok.example.com", "body": "b"},
            ])
            state["installed"] = True

        monkeypatch.delitem(sys.modules, "ddgs", raising=False)
        monkeypatch.setattr(
            web_tools, "_ddgs_package_importable", lambda: state["installed"]
        )
        monkeypatch.setattr(ld, "ensure", fake_ensure)
        monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"backend": "ddgs"})
        monkeypatch.setattr(web_tools, "_is_tool_gateway_ready", lambda: False)
        monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False, raising=False)
        monkeypatch.setattr(web_tools, "_debug", _NoopDebug(), raising=False)
        return state, order

    def test_search_dispatch_triggers_the_install_first(self, monkeypatch):
        """The whole point: a configured-but-absent ddgs self-heals, it does
        not return "run `pip install ddgs"`."""
        from tools import web_tools

        state, order = self._wire(monkeypatch)
        assert state["installed"] is False

        result = json.loads(web_tools.web_search_tool("query", limit=5))

        assert order == ["ensure:search.ddgs"], "install never attempted on dispatch"
        assert result["success"] is True
        assert result["data"]["web"][0]["url"] == "https://ok.example.com"

    def test_search_dispatch_does_not_reinstall_every_call(self, monkeypatch):
        """Cost guard: once importable, dispatch must not re-enter the
        installer — web_search is called many times per session."""
        from tools import web_tools

        state, order = self._wire(monkeypatch)
        json.loads(web_tools.web_search_tool("query", limit=5))
        json.loads(web_tools.web_search_tool("query2", limit=5))

        assert order == ["ensure:search.ddgs"], (
            f"installer re-ran on every search: {order}"
        )


class _NoopDebug:
    """Stand-in for web_tools' DebugSession so the test writes nothing to disk."""

    active = False

    def log_call(self, *args, **kwargs):
        return None

    def save(self, *args, **kwargs):
        return None
