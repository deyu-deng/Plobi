"""The lamp means one thing, and these tests pin down what.

Measured on the live gateway, 2026-11-24:

* Devin answered ``502 "This model is no longer available for your account"`` after
  35.8 s — a refusal, wearing the gateway's status code;
* OpenCode Zen answered ``403 "not available in your country"`` for all ten free ids;
* WorkBuddy answered ``200`` in 1.6 s.

So: a lamp that is on has to have been *lit by an answer*, a refusal has to carry the
upstream's own words into the UI, and a timeout must not be mistaken for either.
"""

from __future__ import annotations

import asyncio

import pytest

from bezel import model_health
from bezel.model_health import record_refusal, record_success, record_unproven
from bezel.providers.apps.magpie.probe import probe_vendor
from bezel.providers.apps.magpie.api import MagpieProvider


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(model_health, "health_path", lambda: tmp_path / "health.json")


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status_code = status
        self.text = body


class _Http:
    """Records which ids were actually asked, so a needless call is a test failure."""

    def __init__(self, *responses: _Response) -> None:
        self._responses = list(responses)
        self.asked: list[str] = []

    async def post(self, url, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self.asked.append(kwargs["json"]["model"])
        return self._responses.pop(0)


def _provider(http: _Http) -> MagpieProvider:
    prov = MagpieProvider({"base_url": "http://127.0.0.1:49754", "api_key": "test"}, http)
    return prov


# ---------------------------------------------------------------- the classifier


def test_a_vendors_verdict_wearing_a_502_is_still_a_verdict():
    """Devin says it in prose; the gateway says it in a status code. Believe the prose."""
    devin = (
        '{"error":{"code":null,"message":"Devin: This model is no longer available for '
        'your account. Please switch to a different model. (HTTP 502, api_error)"}}'
    )
    assert model_health.looks_like_unknown_model(502, devin) is True


def test_a_gateway_that_is_merely_broken_says_nothing_about_a_model():
    """A 502 that never mentions the model must not cost 24 models their lamp."""
    assert model_health.looks_like_unknown_model(502, "connect error: refused") is False
    assert model_health.looks_like_unknown_model(500, '{"error":"internal"}') is False


def test_a_401_is_about_the_key_and_never_about_the_model():
    """Otherwise one bad key turns every id behind the vendor grey — the bug that made
    this whole investigation start."""
    assert model_health.looks_like_unknown_model(401, "invalid api key") is False
    assert model_health.looks_like_unknown_model(403, "invalid api key") is False


# ---------------------------------------------------------------- the ledger


def test_a_timeout_keeps_the_lamp_off_without_quarantining():
    """Not proven is not the same as dead: no cooling clock may start."""
    record_unproven("devin/swe-2", "ReadTimeout")
    assert model_health.quarantined("devin/swe-2") is False
    assert "not proven" in model_health.unproven_reason("devin/swe-2")
    assert "ReadTimeout" in model_health.unproven_reason("devin/swe-2")

    record_success("devin/swe-2")
    assert model_health.unproven_reason("devin/swe-2") == ""


def test_a_refusal_quotes_the_upstream_back_to_the_person_reading_it():
    record_refusal("opencode-zen-free/beam-free", 403, "This model is not available in your country")
    reason = model_health.refusal_reason("opencode-zen-free/beam-free")
    assert "not available in your country" in reason
    assert "back in" in reason


# ---------------------------------------------------------------- the probe


def test_one_seat_refusal_settles_the_whole_vendor_in_a_single_call():
    """Eighteen devin ids cannot each be asked a question already answered. One answer,
    twenty-four grey lamps, and a reason that says why."""
    body = (
        '{"error":{"message":"Devin: This model is no longer available for your account."}}'
    )
    http = _Http(_Response(502, body))
    ids = ["devin/swe-2", "devin/claude-fable-5-2", "devin/gpt-5-1"]

    asyncio.run(probe_vendor(_provider(http), ids, timeout=5.0))

    assert len(http.asked) == 1, "a seat verdict must not be re-asked per sibling"
    for model in ids:
        assert model_health.quarantined(model) is True
        assert "no longer available for your account" in model_health.refusal_reason(model)


def test_an_unanswered_probe_never_starts_a_cooling_clock():
    """A silent vendor is unproven, not banned: the lamp stays off, the door stays open."""
    http = _Http(*[_Response(504, "gateway timeout") for _ in range(3)])

    asyncio.run(probe_vendor(_provider(http), ["magpie/mystery-1"], timeout=5.0))

    assert model_health.quarantined("magpie/mystery-1") is False
    assert "not proven" in model_health.unproven_reason("magpie/mystery-1")


def test_a_vendor_that_answers_keeps_its_lamp():
    http = _Http(_Response(200, '{"choices":[{"message":{"content":"ok"}}]}'))

    asyncio.run(probe_vendor(_provider(http), ["workbuddy/hy4flash-free"], timeout=5.0))

    assert model_health.quarantined("workbuddy/hy4flash-free") is False
    assert model_health.unproven_reason("workbuddy/hy4flash-free") == ""
