"""One model, one row — and the row says what it can change.

Two things the user asked for on 2026-11-01, both measured rather than assumed:

1. **Thinking levels are not three models.** Antigravity's `fetchAvailableModels`
   answers `claude-opus-5-5-low / -medium / -high`; publishing all three put three rows
   in his picker. The catalogue now publishes the family once, and the level comes from
   the request's `reasoning_effort`.
2. **The picker may only offer what the model really has.** Qoder writes per model
   `thinking_config {enabled {efforts {low, medium(default), xhigh}}}` and
   `context_config {200K(default), 400K, 1M}` with `is_editable`; Kiro writes
   `tokenLimits {maxInputTokens, maxOutputTokens}` and no thinking menu at all. Those
   answers ride on `/v1/models` as `tuning`, and a leg that said nothing says nothing
   (`{}`), which the UI reads as "keep the generic controls".

The fixtures below are the shapes captured from those three live catalogues on
2026-11-01 (names and numbers verbatim, `quotaInfo` and internal ids dropped).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from quota_hub.providers.apps.antigravity.api import AntigravityProvider
from quota_hub.providers.apps.kiro.cli import KiroCliProvider
from quota_hub.providers.apps.qoder import oauth
from quota_hub.providers.apps.qoder.api import QoderProvider
from quota_hub.registry import Registry

# --- captured catalogues --------------------------------------------------
AGY_MODELS = {
    "claude-opus-5-5-low": {"displayName": "Claude Opus 5.5 (Low)", "supportsImages": True,
                            "supportsThinking": True, "maxTokens": 1000000, "maxOutputTokens": 128000},
    "claude-opus-5-5-medium": {"displayName": "Claude Opus 5.5 (Medium)", "supportsImages": True,
                               "supportsThinking": True, "thinkingLevel": 2,
                               "maxTokens": 1000000, "maxOutputTokens": 128000},
    "claude-opus-5-5-high": {"displayName": "Claude Opus 5.5 (High)", "supportsImages": True,
                             "supportsThinking": True, "thinkingLevel": 3,
                             "maxTokens": 1000000, "maxOutputTokens": 128000},
    "gpt-oss-120b-medium": {"displayName": "GPT-OSS 120B (Medium)", "supportsThinking": True,
                            "thinkingBudget": 8192, "maxTokens": 131072, "maxOutputTokens": 32768},
    "gemini-3-flash": {"displayName": "Gemini 3 Flash", "supportsImages": True,
                       "supportsThinking": True, "thinkingBudget": -1, "minThinkingBudget": 128,
                       "maxTokens": 1048576, "maxOutputTokens": 65536},
    "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro", "supportsThinking": True,
                       "maxTokens": 1048576, "maxOutputTokens": 65536},
    # Measured 2026-11-01: the newest Gemini row arrives as a *tiered group* — no
    # displayName, `recommended: true`, a 1M window — and the app resolves it into
    # `gemini-3.8-flash-low|-medium|-high` out of its own binary. Dropping every
    # `-tiered` id is what hid the model he could see in Antigravity.
    "gemini-3.8-flash-tiered": {"supportsImages": True, "supportsThinking": True,
                                "thinkingBudget": -1, "minThinkingBudget": 32,
                                "recommended": True, "maxTokens": 1048576,
                                "maxOutputTokens": 65536},
}
AGY_ORDER = list(AGY_MODELS)

QODER_ROWS = [
    {"key": "auto", "enable": True, "is_reasoning": True, "display_name": "Auto",
     "max_input_tokens": 200000, "context_config": {"200K": {"token_count": 200000, "is_default": True}}},
    {"key": "qmodel_38max", "enable": True, "is_reasoning": True, "display_name": "Qwen3.8-Max",
     "max_input_tokens": 180000, "is_editable": True,
     "context_config": {"1M": {"token_count": 1000000},
                        "200K": {"token_count": 200000, "is_default": True},
                        "400K": {"token_count": 400000}},
     "thinking_config": {"disabled": {}, "enabled": {
         "efforts": {"xhigh": {}, "low": {}, "medium": {"is_default": True}}, "is_default": True}}},
]

KIRO_ROWS = [
    {"modelId": "auto", "modelName": "auto", "supportedInputTypes": ["TEXT", "IMAGE"],
     "tokenLimits": {"maxInputTokens": 1000000, "maxOutputTokens": 64000}},
    {"modelId": "deepseek-3.2", "modelName": "deepseek-3.2", "supportedInputTypes": ["TEXT"],
     "tokenLimits": {"maxInputTokens": 164000, "maxOutputTokens": 64000}},
]


class _Stub:
    """One canned httpx response per URL fragment, so nothing leaves the process."""

    def __init__(self, replies: dict[str, tuple[int, object]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, object]] = []

    async def get(self, url, **kw):
        return self._respond(url, kw)

    async def post(self, url, **kw):
        return self._respond(url, kw)

    def _respond(self, url, kw):
        self.calls.append((url, kw.get("json")))
        for needle, (status, payload) in self.replies.items():
            if needle in url:
                return httpx.Response(status, json=payload, request=httpx.Request("POST", url))
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """A temp PLOBI_HOME with a Qoder credential: no vendor call, no real token."""
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    oauth.write_credential({"token": "dt-test", "refresh_token": "rt-test",
                            "user_id": "01a0cd78-93a3-7964-b17f-8ca5992648c0",
                            "expires_at": "2099-01-01T00:00:00Z"})
    return tmp_path


def _agy(payload_models: dict, order: list[str]) -> AntigravityProvider:
    stub = _Stub({":fetchAvailableModels": (200, {"models": payload_models, "agentModelSorts": [
        {"groups": [{"modelIds": [f"models/{m}" for m in order]}]}]})})
    prov = AntigravityProvider({"host": "https://cloudcode-pa.googleapis.com", "accounts": []}, stub)
    prov.ensure_fresh = lambda acc: asyncio.sleep(0)  # type: ignore[method-assign]
    prov._project_id = lambda acc: asyncio.sleep(0, result="p")  # type: ignore[method-assign]
    return prov


def _account(prov, token: str = "T"):
    from quota_hub.providers.base import Account, Credential

    acc = Account(id="a", provider=prov.name, cred=Credential(access_token=token))
    return acc


def _qoder(rows: list[dict]) -> QoderProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/algo/api/v2/model/list"):
            return httpx.Response(200, json={"chat": rows})
        return httpx.Response(404, text="unrouted")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return QoderProvider({"model_prefix": "qoder", "display_name": "Qoder"}, http)


def _kiro(rows: list[dict]) -> KiroCliProvider:
    # Kiro's management door is the bare host: the operation travels in `X-Amz-Target`.
    stub = _Stub({"management": (200, {"models": rows})})
    prov = KiroCliProvider({"binary": "kiro-cli"}, stub)
    prov._auth = lambda: {"access_token": "AT", "region": "us-east-1",  # type: ignore[method-assign]
                          "profile_arn": "arn:aws:kd:1"}
    prov.ensure_fresh = lambda acc: asyncio.sleep(0)  # type: ignore[method-assign]
    prov.binary_path = None
    return prov


# --- Antigravity: tiers ---------------------------------------------------
def test_the_three_thinking_levels_publish_as_one_model():
    prov = _agy(AGY_MODELS, AGY_ORDER)
    acc = _account(prov)
    published = asyncio.run(prov.refresh_models(acc))

    # `published` is the wire-side family list; the names below are what we publish.
    assert published == ["claude-opus-5-5", "gpt-oss-120b-medium", "gemini-3-flash",
                         "gemini-2.5-pro", "gemini-3.8-flash"], published
    assert prov.served_models == (
        "antigravity/claude-opus-5-5", "antigravity/gpt-oss-120b",
        "antigravity/gemini-3-flash", "antigravity/gemini-2.5-pro",
        "antigravity/gemini-3.8-flash"), prov.served_models
    # The name we publish is the vendor's `displayName`; the wire id behind it stays
    # upstream's own — including the tier a single-level row ships with.
    assert prov.MODEL_MAP["antigravity/gpt-oss-120b"] == "gpt-oss-120b-medium", prov.MODEL_MAP
    assert prov.MODEL_MAP["antigravity/gemini-3.8-flash"] == "gemini-3.8-flash", prov.MODEL_MAP


def test_the_request_picks_the_tier_the_turn_asks_for():
    prov = _agy(AGY_MODELS, AGY_ORDER)
    asyncio.run(prov.refresh_models(_account(prov)))

    assert prov._short_model("antigravity/claude-opus-5-5", "high") == "claude-opus-5-5-high"
    assert prov._short_model("antigravity/claude-opus-5-5", "low") == "claude-opus-5-5-low"
    # Nobody chose a level: the vendor's middle, which is also what the status bar prints.
    assert prov._short_model("antigravity/claude-opus-5-5", "") == "claude-opus-5-5-medium"
    # A level this family does not sell clamps along the ladder, not to the default.
    assert prov._short_model("antigravity/claude-opus-5-5", "minimal") == "claude-opus-5-5-low"
    # An id from before the fold keeps routing to itself.
    assert prov._short_model("antigravity/claude-opus-5-5-high", "high") == "claude-opus-5-5-high"
    # And a single-level model stays exactly what upstream called it.
    assert prov._short_model("antigravity/gpt-oss-120b-medium", "high") == "gpt-oss-120b-medium"
    # ...and the row published under its display name reaches the same wire id.
    assert prov._short_model("antigravity/gpt-oss-120b", "high") == "gpt-oss-120b-medium"


def test_a_tiered_turn_asks_upstream_for_the_variant_not_a_thinking_budget():
    prov = _agy(AGY_MODELS, AGY_ORDER)
    acc = _account(prov)
    asyncio.run(prov.refresh_models(acc))
    wrapped = prov._wrap(
        {"model": "antigravity/claude-opus-5-5", "reasoning_effort": "high",
         "messages": [{"role": "user", "content": "hi"}]},
        "p", prov._short_model("antigravity/claude-opus-5-5", "high"), "s")

    assert wrapped["model"] == "claude-opus-5-5-high", wrapped["model"]
    # The tier carries the level; sending a budget on top would ask twice.
    assert "thinkingConfig" not in wrapped["request"].get("generationConfig", {}), wrapped["request"]


def test_the_published_tuning_is_what_the_vendor_wrote():
    prov = _agy(AGY_MODELS, AGY_ORDER)
    asyncio.run(prov.refresh_models(_account(prov)))

    opus = prov.tuning("antigravity/claude-opus-5-5")
    assert opus["efforts"] == ["low", "medium", "high"], opus
    assert opus["default_effort"] == "medium", opus
    assert opus["context"] == {"max": 1000000}, opus
    assert opus["max_output"] == 128000, opus

    # One row, one level: it is not a menu, and the picker is told so.
    oss = prov.tuning("antigravity/gpt-oss-120b")
    assert oss["efforts"] == ["medium"], oss
    assert oss["default_effort"] == "medium", oss

    # A budget model (no tiers, `thinkingBudget: -1` = adaptive) is set by budget, so the
    # four levels `to_gemini` can actually send are the ones it offers.
    gemini = prov.tuning("antigravity/gemini-3-flash")
    assert gemini["efforts"] == ["minimal", "low", "medium", "high"], gemini
    assert gemini["default_effort"] == "medium", gemini



def test_a_tiered_group_is_published_as_the_model_the_app_shows():
    """`gemini-3.8-flash-tiered` is the vendor's newest row, and it had no name to copy.

    With no `displayName` the family id is the name (that is what the app renders), and
    the limits come from the same row — 1M window, 65536 out, thinking by budget.
    """
    prov = _agy(AGY_MODELS, AGY_ORDER)
    asyncio.run(prov.refresh_models(_account(prov)))

    tuning = prov.tuning("antigravity/gemini-3.8-flash")
    assert tuning["context"] == {"max": 1048576}, tuning
    assert tuning["max_output"] == 65536, tuning
    assert tuning["efforts"] == ["minimal", "low", "medium", "high"], tuning
    assert prov._short_model("antigravity/gemini-3.8-flash", "high") == "gemini-3.8-flash"

# --- Qoder: its own effort menu and context menu --------------------------
def test_qoder_publishes_the_levels_and_windows_of_that_model():
    prov = _qoder(QODER_ROWS)
    acc = asyncio.run(prov.discover_accounts())[0]
    asyncio.run(prov.refresh_models(acc))

    qwen = prov.tuning("qoder/qwen3.8-max")
    assert qwen["efforts"] == ["low", "medium", "xhigh"], qwen
    assert qwen["default_effort"] == "medium", qwen
    assert qwen["context"] == {"adjustable": True, "choices": [200000, 400000, 1000000],
                               "default": 200000, "max": 1000000}, qwen

    # `Auto` writes no thinking menu and one window: nothing is inferred for it.
    auto = prov.tuning("qoder/auto")
    assert "efforts" not in auto, auto
    assert auto["context"] == {"adjustable": False, "choices": [200000],
                               "default": 200000, "max": 200000}, auto


def test_qoder_clamps_the_level_and_the_window_to_what_the_model_sells():
    prov = _qoder(QODER_ROWS)
    acc = asyncio.run(prov.discover_accounts())[0]
    asyncio.run(prov.refresh_models(acc))
    key, body = prov._body({"model": "qoder/qwen3.8-max", "reasoning_effort": "high",
                            "context_length": 900000,
                            "messages": [{"role": "user", "content": "hi"}]}, "rec")

    assert key == "qmodel_38max", key
    # `high` is not on Qwen3.8-Max's menu (low/medium/xhigh) → nearest rung: xhigh.
    assert body["parameters"]["reasoning_effort"] == "xhigh", body["parameters"]
    # 900K is not a window it sells: take the largest one at or below it, never a bigger
    # number than the caller budgeted for.
    assert body["model_config"]["max_input_tokens"] == 400000, body["model_config"]

    _, tiny = prov._body({"model": "qoder/qwen3.8-max", "context_length": 50000,
                          "messages": [{"role": "user", "content": "hi"}]}, "rec0")
    # Nothing that small exists on this menu → its smallest, and that is said plainly by
    # the fact that the turn still runs.
    assert tiny["model_config"]["max_input_tokens"] == 200000, tiny["model_config"]

    _, off = prov._body({"model": "qoder/auto", "reasoning_effort": "none",
                         "messages": [{"role": "user", "content": "hi"}]}, "rec2")
    assert off["parameters"]["enable_thinking"] is False, off["parameters"]
    assert off["model_config"]["max_input_tokens"] == 200000, off["model_config"]


# --- Kiro: limits, and no invented effort menu ----------------------------
def test_kiro_publishes_its_limits_and_names_no_effort_menu():
    prov = _kiro(KIRO_ROWS)
    acc = _account(prov)
    published = asyncio.run(prov.refresh_models(acc))

    assert published == ["auto", "deepseek-3.2"], published
    assert prov.served_models == ("kiro_cli/auto", "kiro_cli/deepseek-3.2"), prov.served_models
    deepseek = prov.tuning("kiro_cli/deepseek-3.2")
    assert deepseek["context"] == {"max": 164000}, deepseek
    assert deepseek["max_output"] == 64000, deepseek
    assert "efforts" not in deepseek, "Kiro publishes no thinking levels; nothing is invented"


# --- the catalog row carries it -------------------------------------------
def test_the_models_endpoint_puts_the_answer_on_every_row():
    class _Leg:
        name = "demo"
        accounts: list = []
        served_models = ("demo/one",)

        def capabilities(self):
            from quota_hub.providers.base import Capabilities

            return Capabilities(stream=True, tools=True)

        def credential_health(self):
            return True, "ok"

        def tuning(self, model):
            return {"efforts": ["low", "high"], "context": {"max": 1234}}

    reg = Registry({"providers": {}}, None)
    leg = _Leg()
    reg.providers["demo"] = leg
    reg.model_to_provider = {"demo/one": "demo"}

    row = next(r for r in reg.list_models() if r["id"] == "demo/one")

    assert row["tuning"] == {"efforts": ["low", "high"], "context": {"max": 1234}}, row
    assert row["provider"] == "demo", "the row's provider is the card id the picker joins on"


def test_a_leg_that_says_nothing_leaves_the_row_empty_rather_than_wrong():
    class _Quiet(_Stub):
        pass

    reg = Registry({"providers": {}}, None)
    reg.providers["quiet"] = AntigravityProvider({"host": "https://x", "accounts": []}, _Quiet({}))
    reg.model_to_provider = {"antigravity/none": "quiet"}

    row = next(r for r in reg.list_models() if r["id"] == "antigravity/none")

    assert row["tuning"] == {}, "no answer must not read as 'no knobs'"
