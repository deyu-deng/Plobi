"""The picker must only ever see what the hub is vouching for right now.

Measured 2026-10-27: with the hub not running at all, the model picker still listed
35 hub models read out of `$PLOBI_HOME/provider_models_cache.json`, 17 of which the
hub itself had already judged unusable (expired credential, text-only transport).
These are the rules that make that state unreachable.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import plobi_cli.models as models

_ROWS = [
    {"id": "workbuddy/hy3", "selectable": True},
    {"id": "antigravity/gemini-3-pro-high", "selectable": False},
]


class _Hub(BaseHTTPRequestHandler):
    """A stand-in hub that records what the picker actually asked for."""

    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append(self.path)
        assert self.headers.get("Authorization") == "Bearer test-key"
        body = json.dumps({"object": "list", "data": _ROWS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence stderr noise during tests
        return


def _serve():
    server = HTTPServer(("127.0.0.1", 0), _Hub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_hub_catalog_is_asked_for_only_selectable_models():
    server = _serve()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        ids = models.hub_selectable_model_ids("test-key", base)
    finally:
        server.shutdown()

    assert ids == ["workbuddy/hy3", "antigravity/gemini-3-pro-high"]
    assert any("selectable=1" in path for path in _Hub.seen), _Hub.seen


def test_unreachable_hub_answers_none_not_empty():
    # "we could not ask" and "the hub has nothing" are different claims; the caller
    # decides what to do with the first one, so it must be distinguishable.
    assert models.hub_selectable_model_ids("k", "http://127.0.0.1:1/v1", timeout=1.0) is None


def test_the_hub_catalog_is_never_read_from_disk_cache(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        models,
        "provider_model_ids",
        lambda provider, **kwargs: calls.append(provider) or [],
    )
    # A fresh, non-empty cached entry that must be ignored.
    monkeypatch.setattr(
        models,
        "_load_provider_models_cache",
        lambda: {"aigw": {"fp": models._credential_fingerprint("aigw"), "models": ["dead/model"], "ts": 9e12}},
    )

    assert models.cached_provider_model_ids("aigw") == []
    assert calls == ["aigw"]


def test_signed_out_acp_legs_cached_catalog_does_not_survive(monkeypatch):
    """The login verdict is part of the cache identity for an ACP leg."""

    class _Registry:
        def get(self, key):
            return type(
                "P",
                (),
                {"auth_type": "external_process", "api_key_env_vars": (), "base_url_env_var": ""},
            )()

    monkeypatch.setattr("plobi_cli.auth.PROVIDER_REGISTRY", _Registry())
    monkeypatch.setattr(
        "plobi_cli.auth.external_process_login_state",
        lambda provider, **kwargs: {"logged_in": False, "models": ()},
    )
    signed_out = models._credential_fingerprint("gemini-acp")

    monkeypatch.setattr(
        "plobi_cli.auth.external_process_login_state",
        lambda provider, **kwargs: {"logged_in": True, "models": ("gemini-3.1-pro-preview",)},
    )
    signed_in = models._credential_fingerprint("gemini-acp")

    assert signed_out != signed_in
