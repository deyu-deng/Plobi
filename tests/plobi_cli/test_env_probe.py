"""POST /api/env/probe — the "Test" button behind both settings surfaces.

The registry in ``plobi_cli/env_probe.py`` is the only place a probe is defined.
Model provider keys are checked with an OpenAI-shaped ``/v1/models`` read,
translation engines with their own call shape: DeepL wants a POSTed form with an
``Authorization: DeepL-Auth-Key`` header, a self-hosted endpoint a GET with the
text in ``q``. ``POST /api/providers/validate`` is a thin alias over the same
registry, so the cases below cover both doors — and adding a credential no
longer means keeping two implementations in step.

Before the probe existed the UI could only say "saved" — a wrong DeepL key
looked exactly like a working one until every generated English name came back
empty.

These tests pin the three-state contract the SPA relies on:

* ``ok=True``                                    — credential accepted;
* ``ok=False, reachable=True``                   — credential is wrong;
* ``reachable=False``                            — the probe could not run, so
  the UI must NOT say "wrong key".

They also pin that ``GET /api/env`` only flags rows that really have a probe.
"""

import json

import httpx
from fastapi.testclient import TestClient

import plobi_cli.web_server as web_server
from plobi_cli.env_probe import DEEPL_KEY_ENV, LOCAL_URL_ENV

def _client() -> TestClient:
    """A client per request, bound to the *current* web_server module.

    A cached client would carry the session token it was built with, and other
    test modules reload ``web_server`` to rotate that token — the request would
    come back 401 for reasons that have nothing to do with the probe.
    """
    return TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )


class _Resp:
    """Minimal stand-in for httpx.Response (only what the probes touch)."""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(payload) if payload is not None else ""

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")

        return self._payload


def _patch_httpx(monkeypatch, response=None, exc=None):
    """Swap httpx.Client for a recorder; returns the list of calls made."""
    calls = []

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def _record(self, method, url, kwargs):
            calls.append({"method": method, "url": url, **kwargs})

            if exc is not None:
                raise exc

            return response

        def post(self, url, **kwargs):
            return self._record("POST", url, kwargs)

        def get(self, url, **kwargs):
            return self._record("GET", url, kwargs)

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    return calls


def _probe(key, value=""):
    resp = _client().post("/api/env/probe", json={"key": key, "value": value})
    assert resp.status_code == 200

    return resp.json()


# --------------------------------------------------------------------------- #
# /api/env advertises which rows are testable
# --------------------------------------------------------------------------- #
def _env_rows(monkeypatch, env_on_disk=None):
    monkeypatch.setattr(web_server, "load_env", lambda: dict(env_on_disk or {}))
    monkeypatch.setattr(web_server, "_channel_managed_env_keys", lambda: set())
    resp = _client().get("/api/env")
    assert resp.status_code == 200

    return resp.json()


def test_translation_rows_advertise_probe(monkeypatch):
    rows = _env_rows(monkeypatch)

    assert rows[DEEPL_KEY_ENV]["probe"] is True
    assert rows[LOCAL_URL_ENV]["probe"] is True


def test_unprobeable_row_does_not_advertise_probe(monkeypatch):
    rows = _env_rows(monkeypatch, {"MY_OWN_KEY": "abc", "OPENAI_API_KEY": "sk-x"})

    # A custom key has no probe. A provider key does: both surfaces read the same
    # registry (env_probe._PROBES), so the row offers the same Test button rather
    # than making the user switch pages to find out whether the key works.
    assert rows["MY_OWN_KEY"]["probe"] is False
    assert rows["OPENAI_API_KEY"]["probe"] is True


# --------------------------------------------------------------------------- #
# Endpoint guards
# --------------------------------------------------------------------------- #
def test_unsupported_key_reports_not_supported():
    payload = _probe("MY_OWN_KEY", "abc")

    assert payload["supported"] is False
    assert payload["ok"] is False


def test_empty_value_is_a_hint_not_a_verdict(monkeypatch):
    monkeypatch.setattr(web_server, "load_env", lambda: {})
    # A key exported in the developer's own shell must not leak into this case.
    monkeypatch.delenv(DEEPL_KEY_ENV, raising=False)

    payload = _probe(DEEPL_KEY_ENV, "")

    assert payload["supported"] is True
    assert payload["ok"] is False
    assert payload["reachable"] is True
    assert "Enter a value" in payload["message"]


def test_saved_value_is_used_when_no_draft_is_sent(monkeypatch):
    """Testing a saved key: the SPA sends no value, so the probe reads .env."""
    monkeypatch.setattr(web_server, "load_env", lambda: {DEEPL_KEY_ENV: "abc:fx"})
    calls = _patch_httpx(
        monkeypatch, _Resp(200, {"translations": [{"text": "Hello"}]})
    )

    payload = _probe(DEEPL_KEY_ENV, "")

    assert payload["ok"] is True
    assert calls[0]["headers"]["Authorization"] == "DeepL-Auth-Key abc:fx"


# --------------------------------------------------------------------------- #
# DeepL
# --------------------------------------------------------------------------- #
def test_deepl_free_key_hits_the_free_host(monkeypatch):
    """Free keys only work on api-free — the probe must use the same host."""
    calls = _patch_httpx(
        monkeypatch, _Resp(200, {"translations": [{"text": "Hello"}]})
    )

    payload = _probe(DEEPL_KEY_ENV, "0123-abcd:fx")

    assert payload["ok"] is True
    assert payload["reachable"] is True
    assert payload["sample"] == "Hello"
    assert calls[0]["url"] == "https://api-free.deepl.com/v2/translate"


def test_deepl_pro_key_hits_the_paid_host(monkeypatch):
    calls = _patch_httpx(
        monkeypatch, _Resp(200, {"translations": [{"text": "Hello"}]})
    )

    _probe(DEEPL_KEY_ENV, "0123-abcd")

    assert calls[0]["url"] == "https://api.deepl.com/v2/translate"


def test_deepl_rejected_key_says_so(monkeypatch):
    # DeepL answers 403 (not 401) for a key it won't accept.
    _patch_httpx(monkeypatch, _Resp(403))

    payload = _probe(DEEPL_KEY_ENV, "nope")

    assert payload["ok"] is False
    assert payload["reachable"] is True, "a 403 is a verdict, not an outage"
    assert "rejected" in payload["message"]


def test_deepl_quota_exhausted_is_distinguished_from_bad_key(monkeypatch):
    # 456 = quota used up: the key is right, so don't tell the user to fix it.
    _patch_httpx(monkeypatch, _Resp(456))

    payload = _probe(DEEPL_KEY_ENV, "0123-abcd")

    assert payload["ok"] is False
    assert payload["reachable"] is True
    assert "quota" in payload["message"]


def test_deepl_rate_limit_still_counts_as_accepted(monkeypatch):
    _patch_httpx(monkeypatch, _Resp(429))

    payload = _probe(DEEPL_KEY_ENV, "0123-abcd")

    assert payload["ok"] is True
    assert payload["reachable"] is True


def test_deepl_network_failure_is_not_a_wrong_key(monkeypatch):
    _patch_httpx(monkeypatch, exc=httpx.ConnectError("boom"))

    payload = _probe(DEEPL_KEY_ENV, "0123-abcd")

    assert payload["ok"] is False
    assert payload["reachable"] is False


def test_deepl_success_without_translation_is_not_ok(monkeypatch):
    _patch_httpx(monkeypatch, _Resp(200, {"translations": []}))

    payload = _probe(DEEPL_KEY_ENV, "0123-abcd")

    assert payload["ok"] is False
    assert payload["reachable"] is True


# --------------------------------------------------------------------------- #
# Self-hosted endpoint
# --------------------------------------------------------------------------- #
def test_local_url_without_scheme_is_caught_before_the_call(monkeypatch):
    calls = _patch_httpx(monkeypatch)

    payload = _probe(LOCAL_URL_ENV, "127.0.0.1:5000/translate")

    assert payload["ok"] is False
    assert payload["reachable"] is False
    assert "http://" in payload["message"]
    assert calls == [], "should not attempt a request with no scheme"


def test_local_endpoint_translates(monkeypatch):
    calls = _patch_httpx(
        monkeypatch, _Resp(200, {"translatedText": "Hello"})
    )

    payload = _probe(LOCAL_URL_ENV, "http://127.0.0.1:5000/translate")

    assert payload["ok"] is True
    # "translatedText" is the stock LibreTranslate spelling — it used to be
    # missing from the parser, so a working endpoint looked like a broken one.
    assert payload["sample"] == "Hello"
    # Both parameter spellings go out, same as naming.translate_local.
    assert "q=%E4%BD%A0%E5%A5%BD" in calls[0]["url"]
    assert "source=auto" in calls[0]["url"]


def test_local_endpoint_trailing_slash_does_not_double_up(monkeypatch):
    calls = _patch_httpx(
        monkeypatch, _Resp(200, text="Hello")
    )

    payload = _probe(LOCAL_URL_ENV, "http://127.0.0.1:5000/translate/")

    assert payload["ok"] is True
    assert payload["sample"] == "Hello"
    assert calls[0]["url"].startswith("http://127.0.0.1:5000/translate?q=")


def test_local_nothing_listening_points_at_the_service(monkeypatch):
    _patch_httpx(monkeypatch, exc=httpx.ConnectError("refused"))

    payload = _probe(LOCAL_URL_ENV, "http://127.0.0.1:5000/translate")

    assert payload["ok"] is False
    assert payload["reachable"] is False
    assert "listening" in payload["message"]


def test_local_http_error_is_reachable_but_not_ok(monkeypatch):
    _patch_httpx(monkeypatch, _Resp(500))

    payload = _probe(LOCAL_URL_ENV, "http://127.0.0.1:5000/translate")

    assert payload["ok"] is False
    assert payload["reachable"] is True
    assert "500" in payload["message"]


def test_local_reply_without_a_translation_is_not_ok(monkeypatch):
    _patch_httpx(monkeypatch, _Resp(200, {"status": "ok"}, text='{"status": "ok"}'))

    payload = _probe(LOCAL_URL_ENV, "http://127.0.0.1:5000/translate")

    assert payload["ok"] is False
    assert payload["reachable"] is True


# --------------------------------------------------------------------------- #
# Model-provider keys (same registry, so the same three states)
# --------------------------------------------------------------------------- #
def test_provider_key_is_probeable_now():
    payload = _probe("OPENAI_API_KEY", "")

    assert payload["supported"] is True


def test_provider_key_accepted(monkeypatch):
    calls = _patch_httpx(monkeypatch, _Resp(200, {"data": [{"id": "gpt-4o"}]}))

    payload = _probe("OPENAI_API_KEY", "sk-good")

    assert payload["ok"] is True
    assert payload["reachable"] is True
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-good"


def test_gemini_key_goes_in_the_query(monkeypatch):
    calls = _patch_httpx(monkeypatch, _Resp(200, {"models": []}))

    payload = _probe("GEMINI_API_KEY", "AIza-good")

    assert payload["ok"] is True
    assert calls[0]["params"]["key"] == "AIza-good"
    assert "Authorization" not in calls[0]["headers"]


def test_provider_rejected_key_is_reachable_but_not_ok(monkeypatch):
    _patch_httpx(monkeypatch, _Resp(401))

    payload = _probe("XAI_API_KEY", "xai-bad")

    assert payload["ok"] is False
    assert payload["reachable"] is True
    assert "rejected" in payload["message"]


def test_provider_rate_limit_does_not_block_saving(monkeypatch):
    _patch_httpx(monkeypatch, _Resp(429))

    payload = _probe("OPENROUTER_API_KEY", "sk-or-throttled")

    assert payload["ok"] is True


def test_provider_network_failure_is_not_a_wrong_key(monkeypatch):
    _patch_httpx(monkeypatch, exc=httpx.ConnectError("boom"))

    payload = _probe("OPENAI_API_KEY", "sk-whatever")

    assert payload["ok"] is False
    assert payload["reachable"] is False


def test_base_url_probe_reports_the_served_models(monkeypatch):
    calls = _patch_httpx(monkeypatch, _Resp(200, {"data": [{"id": "gpt-oss-120b"}]}))

    payload = _probe("OPENAI_BASE_URL", "https://text.example.com/v1/")

    assert payload["ok"] is True
    assert payload["models"] == ["gpt-oss-120b"]
    assert calls[0]["url"] == "https://text.example.com/v1/models"
    assert calls[0]["headers"] is None


def test_base_url_probe_forwards_the_optional_api_key(monkeypatch):
    calls = _patch_httpx(monkeypatch, _Resp(200, {"data": [{"id": "m"}]}))
    resp = _client().post(
        "/api/env/probe",
        json={"key": "OPENAI_BASE_URL", "value": "https://text.example.com/v1", "api_key": "sk-secret"},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls[0]["headers"] == {"Authorization": "Bearer sk-secret"}
