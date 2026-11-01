"""The subscription gateway sends the user's thinking level — in OpenAI's own field.

Measured 2026-11-01 on his machine: the 思考强度 control in the model popover wrote
`agent.reasoning_effort`, and the generic chat-completions transport only emits a
reasoning field on gated routes — OpenRouter (by model prefix), Nous, GitHub, LM Studio,
Kimi, TokenHub. A loopback base_url hits none of them, and
`Agent._supports_reasoning_extra_body()` answers False for it (`if "openrouter" not in
self._base_url_lower: return False`), so **the level never left the desktop**. That
matters most on the legs where the level is part of the model name — Antigravity sells
`claude-opus-5-5-low|-medium|-high` — because without the field the hub could not choose
which row to run.

The fix lives on this profile, not on that gate: the hub is our own door and reads
`reasoning_effort`, so the profile that fronts it declares the field. The gate stays
untouched for every third-party provider, whose rejections it exists to avoid.
"""
from __future__ import annotations

import pytest

from providers import get_provider_profile


@pytest.fixture(scope="module")
def aigw_profile():
    profile = get_provider_profile("aigw")
    assert profile is not None, "the quota gateway profile must stay registered"
    return profile


class TestAigwEffortOnTheWire:
    def test_a_chosen_level_travels_as_the_openai_field(self, aigw_profile):
        extra_body, top_level = aigw_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "high"}
        )

        assert top_level == {"reasoning_effort": "high"}, top_level
        assert extra_body == {}, "no provider-private envelope on our own door"

    @pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max"])
    def test_every_level_plobi_writes_is_accepted(self, aigw_profile, effort):
        """The vocabulary is `plobi_constants.VALID_REASONING_EFFORTS`, not a subset."""
        _extra, top = aigw_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": effort}
        )

        assert top == {"reasoning_effort": effort}, top

    def test_thinking_off_sends_no_level(self, aigw_profile):
        """"Off" is the absence of a level, not a level: the leg keeps the vendor's own."""
        _extra, top = aigw_profile.build_api_kwargs_extras(reasoning_config={"enabled": False})

        assert top == {}, top

    @pytest.mark.parametrize("config", [None, {}, {"enabled": True}, {"enabled": True, "effort": ""},
                                        {"enabled": True, "effort": "impossible"}])
    def test_nothing_is_invented_when_no_level_was_named(self, aigw_profile, config):
        _extra, top = aigw_profile.build_api_kwargs_extras(reasoning_config=config)

        assert top == {}, top


class TestAigwThroughTheTransport:
    """The whole path: profile → the kwargs a request is actually built with."""

    def test_the_level_reaches_the_hub_request(self):
        from agent.transports import get_transport

        transport = get_transport("chat_completions")
        profile = get_provider_profile("aigw")

        kwargs = transport.build_kwargs(
            model="antigravity/claude-opus-5-5",
            messages=[{"role": "user", "content": "hi"}],
            provider_profile=profile,
            reasoning_config={"enabled": True, "effort": "low"},
            # The gate the legacy path uses. It is False for a loopback base_url, which is
            # exactly why this used to answer without the level — the profile path must not
            # inherit it.
            supports_reasoning=False,
            base_url="http://127.0.0.1:8000/v1",
        )

        assert kwargs["reasoning_effort"] == "low", kwargs
        assert "reasoning" not in kwargs.get("extra_body", {}), kwargs
