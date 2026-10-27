"""Verification for the per-model evidence ledger (`quota_hub/model_health.py`).

The hole it fills: a leg's `models:` list in `config.yaml` is a claim, not a
measurement. When an upstream renames or drops a model, the hub kept offering the old id
and the user found out by trying it — which is the one thing the model box must not do
(user 2026-10-27: 「模型框里面应该显示真正能用的模型」).

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_model_health.py
"""

from __future__ import annotations

import asyncio
import time

import httpx
from quota_hub import model_health
from quota_hub.providers.base import Account, Capabilities, Provider, UpstreamError
from quota_hub.registry import Registry
from quota_hub.scheduler import Scheduler

KEY = "sk-test"


class _Refusing(Provider):
    """One model id, which the upstream says it does not serve."""

    name = "refusing"
    served_models = ("refusing/gone",)
    default_capabilities = Capabilities(stream=True, tools=True)

    def __init__(self):
        self.calls = 0
        self.accounts = [Account(id="a", provider=self.name)]

    def capabilities(self):
        return Capabilities(stream=True, tools=True)

    async def ensure_fresh(self, acc):
        """The base class makes this abstract; this fake has no token to renew."""

    async def chat(self, acc, oai_req):
        self.calls += 1
        raise UpstreamError(404, "model 'refusing/gone' is not served by this account")

    async def chat_stream(self, acc, oai_req):  # pragma: no cover - unused
        raise UpstreamError(404, "model 'refusing/gone' is not served by this account")


# ---------------------------------------------------------------- classifier
def test_only_refusals_about_the_model_id_count():
    assert model_health.looks_like_unknown_model(404, "model 'x' not found") is True
    assert model_health.looks_like_unknown_model(400, "invalid model specified") is True
    assert model_health.looks_like_unknown_model(404, "模型不存在") is True


def test_rate_limit_and_timeout_are_not_evidence_about_a_name():
    # Removing a perfectly good name because the account is throttled would be a
    # worse lie than the one this ledger fixes.
    assert model_health.looks_like_unknown_model(429, "rate limit exceeded") is False
    assert model_health.looks_like_unknown_model(504, "upstream timeout") is False
    assert model_health.looks_like_unknown_model(400, "content policy") is False
    assert model_health.looks_like_unknown_model("boom", "no such model") is False


# ------------------------------------------------------------------ thresholds
def test_one_refusal_is_a_hiccup_two_is_a_pattern():
    assert model_health.record_refusal("wb/x", 404, "model not found") is False
    assert model_health.quarantined("wb/x") is False
    assert model_health.record_refusal("wb/x", 404, "model not found") is True
    assert model_health.quarantined("wb/x") is True


def test_a_refusal_of_the_wrong_kind_never_quarantines():
    for _ in range(5):
        model_health.record_refusal("wb/y", 429, "rate limit")

    assert model_health.quarantined("wb/y") is False


def test_a_turn_that_came_back_clears_the_suspicion():
    model_health.record_refusal("wb/z", 404, "model not found")
    model_health.record_refusal("wb/z", 404, "model not found")
    assert model_health.quarantined("wb/z") is True

    model_health.record_success("wb/z")

    assert model_health.quarantined("wb/z") is False
    assert model_health.served("wb/z") is True


def test_the_quarantine_ends_on_its_own():
    model_health.record_refusal("wb/t", 404, "model not found")
    model_health.record_refusal("wb/t", 404, "model not found", cooldown=0.05)

    assert model_health.quarantined("wb/t") is True
    time.sleep(0.06)
    assert model_health.quarantined("wb/t") is False


def test_a_torn_health_file_reads_as_no_opinions(tmp_path, monkeypatch):
    path = tmp_path / "model-health.json"
    monkeypatch.setenv(model_health.MODEL_HEALTH_ENV, str(path))
    path.write_text("{oops", encoding="utf-8")

    assert model_health.quarantined("wb/x") is False
    # and a later write still lands
    model_health.record_refusal("wb/x", 404, "model not found")
    model_health.record_refusal("wb/x", 404, "model not found")
    assert model_health.quarantined("wb/x") is True


# ----------------------------------------------------------------- integration
def test_traffic_alone_moves_a_dead_id_out_of_the_pickable_view():
    """Two real dispatches that the upstream refused ⇒ the id is no longer offered."""

    async def _go():
        prov = _Refusing()
        providers = {prov.name: prov}
        sched = Scheduler(providers)

        for _ in range(2):
            try:
                await sched.dispatch(prov.name, {"model": "refusing/gone", "messages": []}, None, stream=False)
            except UpstreamError:
                pass

        assert model_health.quarantined("refusing/gone") is True

        # The registry view is what `/v1/models?selectable=1` serves the desktop with.
        cfg = {"server": {"api_key": KEY}, "providers": {}, "routing": {"rules": []}}
        async with httpx.AsyncClient() as dummy:
            reg = Registry(cfg, dummy)
            reg.providers = providers
            reg.model_to_provider = {"refusing/gone": prov.name}

            full = {row["id"]: row for row in reg.list_models()}
            strict = {row["id"] for row in reg.list_models(selectable_only=True)}

        assert "refusing/gone" in full
        assert full["refusing/gone"]["unselectable_reason"] == "model_rejected"
        assert "refusing/gone" not in strict

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_successful_turn_is_positive_evidence_for_that_id():
    """Same path, opposite verdict: content came back, so nothing is quarantined."""

    class _Serving(_Refusing):
        async def chat(self, acc, oai_req):
            self.calls += 1
            return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}

    async def _go():
        prov = _Serving()
        sched = Scheduler({prov.name: prov})
        result = await sched.dispatch(prov.name, {"model": "refusing/gone", "messages": []}, None, stream=False)

        assert result["choices"][0]["message"]["content"] == "hi"
        assert model_health.served("refusing/gone") is True
        assert model_health.quarantined("refusing/gone") is False

    asyncio.run(asyncio.wait_for(_go(), timeout=30))
