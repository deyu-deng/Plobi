"""The model confirmer is the first real consumer of plobi/routing.

These tests exist to keep two contracts honest:

- ADR-0010 — only the clipped snippet reaches the model, never the raw message.
- ADR-0011 — confirmation is L2 work; L1 and GUI-only surfaces are rejected.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime

import pytest

from plobi.agenda.rules import RuleHit, match
from plobi.collectors.chatlog.client import ChatMessage
from plobi.collectors.chatlog.confirm import ConfirmContext, HeuristicConfirmer
from plobi.collectors.chatlog.model_confirm import (
    FallbackConfirmer,
    ModelConfirmer,
    _extract_json,
    _to_candidate,
)
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.state import SeenStore
from plobi.agenda.service import AgendaService
from plobi.routing import L1_SECRETARY, L2_AGENDA, ModelRoute, ModelRouter, RoutingError

NOW = datetime(2026, 8, 25, 10, 0)


class RecordingCompleter:
    """Captures what would have been sent, and returns a canned reply."""

    def __init__(self, reply: str | None = '{"date":"2026-08-26","time":"15:00","title":"组会"}'):
        self.reply = reply
        self.prompts: list[str] = []
        self.routes: list[ModelRoute] = []

    def __call__(self, prompt: str, route: ModelRoute) -> str | None:
        self.prompts.append(prompt)
        self.routes.append(route)
        return self.reply


def message(content: str, *, talker: str = "班级群", msg_id: str = "m1") -> ChatMessage:
    return ChatMessage(
        msg_id=msg_id,
        talker=talker,
        sender="导师",
        sent_at="2026-08-25 10:00:00",
        content=content,
    )


def router_with(**overrides: ModelRoute) -> ModelRouter:
    router = ModelRouter.load()
    for role, route in overrides.items():
        router.routes[role] = route
    return router


# --- routing is genuinely consumed -----------------------------------------


def test_confirmer_uses_the_l2_route_not_a_hardcoded_model():
    completer = RecordingCompleter()
    confirmer = ModelConfirmer(
        completer=completer,
        router=router_with(
            **{L2_AGENDA: ModelRoute(role=L2_AGENDA, provider="deepseek", model="deepseek-chat")}
        ),
        now_factory=lambda: NOW,
    )

    hit = match("明天下午三点开会") or RuleHit()
    confirmer.confirm(message("明天下午三点开会"), hit)

    assert completer.routes, "the confirmer never resolved a route"
    assert completer.routes[0].qualified == "deepseek/deepseek-chat"


def test_a_changed_route_changes_which_model_is_called():
    """Proof the routing table is read, not bypassed."""
    completer = RecordingCompleter()
    confirmer = ModelConfirmer(
        completer=completer,
        router=router_with(
            **{L2_AGENDA: ModelRoute(role=L2_AGENDA, provider="moonshot", model="kimi-k3")}
        ),
        now_factory=lambda: NOW,
    )

    hit = match("明天下午三点开会") or RuleHit()
    confirmer.confirm(message("明天下午三点开会"), hit)

    assert completer.routes[0].qualified == "moonshot/kimi-k3"


def test_wiring_the_secretary_here_is_rejected():
    """ADR-0011: the flagship must not be pulled into per-message work."""
    confirmer = ModelConfirmer(
        completer=RecordingCompleter(),
        router=router_with(),
        role=L1_SECRETARY,
        now_factory=lambda: NOW,
    )

    with pytest.raises(RoutingError, match="secretary"):
        confirmer.route()

    hit = match("明天下午三点开会") or RuleHit()
    assert confirmer.confirm(message("明天下午三点开会"), hit) is None


def test_a_gui_only_surface_is_rejected():
    confirmer = ModelConfirmer(
        completer=RecordingCompleter(),
        router=router_with(
            **{L2_AGENDA: ModelRoute(role=L2_AGENDA, provider="marvis", model="gui")}
        ),
        now_factory=lambda: NOW,
    )

    with pytest.raises(RoutingError, match="GUI-only"):
        confirmer.route()


def test_a_broken_route_degrades_to_unresolved_instead_of_raising():
    """The pipeline must keep running even when routing is misconfigured."""
    confirmer = ModelConfirmer(
        completer=RecordingCompleter(),
        router=router_with(
            **{L2_AGENDA: ModelRoute(role=L2_AGENDA, provider="marvis", model="gui")}
        ),
        now_factory=lambda: NOW,
    )

    hit = match("明天下午三点开会") or RuleHit()
    assert confirmer.confirm(message("明天下午三点开会"), hit) is None


# --- privacy boundary (ADR-0010) -------------------------------------------


def test_only_the_snippet_reaches_the_model():
    completer = RecordingCompleter()
    confirmer = ModelConfirmer(completer=completer, router=router_with(), now_factory=lambda: NOW)

    # Long enough that snippet() clips it, and carrying text outside the clip.
    long_message = "同学们注意 " + "明天下午三点开会 " * 60 + " 另外这是不该外发的内容XYZ"
    hit = match(long_message) or RuleHit()
    confirmer.confirm(message(long_message), hit)

    prompt = completer.prompts[0]
    assert "不该外发的内容XYZ" not in prompt
    assert len(prompt) < 900, "prompt leaked the untrimmed message"


def test_no_completer_means_no_candidate():
    confirmer = ModelConfirmer(completer=None, router=router_with(), now_factory=lambda: NOW)

    hit = match("明天下午三点开会") or RuleHit()
    assert confirmer.confirm(message("明天下午三点开会"), hit) is None


def test_a_failing_call_degrades_to_unresolved():
    def boom(prompt: str, route: ModelRoute) -> str:
        raise urllib.error.URLError("connection refused")

    confirmer = ModelConfirmer(completer=boom, router=router_with(), now_factory=lambda: NOW)

    hit = match("明天下午三点开会") or RuleHit()
    assert confirmer.confirm(message("明天下午三点开会"), hit) is None


# --- reply parsing ----------------------------------------------------------


def test_a_well_formed_reply_becomes_a_candidate():
    completer = RecordingCompleter('{"date":"2026-08-26","time":"15:00","title":"组会"}')
    confirmer = ModelConfirmer(completer=completer, router=router_with(), now_factory=lambda: NOW)

    hit = match("明天下午三点开会") or RuleHit()
    candidate = confirmer.confirm(message("明天下午三点开会"), hit)

    assert candidate is not None
    assert candidate.start_at == "2026-08-26T15:00:00"
    assert candidate.title == "组会"
    assert candidate.kind == hit.category


def test_a_null_date_is_not_invented():
    confirmer = ModelConfirmer(
        completer=RecordingCompleter('{"date":null,"time":"15:00","title":"组会"}'),
        router=router_with(),
        now_factory=lambda: NOW,
    )

    hit = match("明天下午三点开会") or RuleHit()
    assert confirmer.confirm(message("明天下午三点开会"), hit) is None


def test_a_missing_clock_is_left_unresolved_instead_of_a_fake_default():
    """WP-EXTRACT-CONTEXT removed the ``_DEFAULT_CLOCK`` fill.

    A date with no stated clock is genuinely unresolved: better an honest
    "unresolved" than a made-up 09:00/23:59 the user has to correct. So the
    confirmer must refuse here, not invent a time.
    """
    hit = RuleHit(matched_keywords=["截止"], category="ddl")
    confirmer = ModelConfirmer(
        completer=RecordingCompleter('{"date":"2026-08-26","time":null,"title":"交表"}'),
        router=router_with(),
        now_factory=lambda: NOW,
    )

    assert confirmer.confirm(message("8月26日截止交表"), hit) is None


def test_prose_around_the_json_is_tolerated():
    confirmer = ModelConfirmer(
        completer=RecordingCompleter(
            '好的，结果是：\n```json\n{"date":"2026-08-26","time":"09:30","title":"答辩"}\n```\n'
        ),
        router=router_with(),
        now_factory=lambda: NOW,
    )

    hit = match("明天上午九点半答辩") or RuleHit()
    candidate = confirmer.confirm(message("明天上午九点半答辩"), hit)

    assert candidate is not None
    assert candidate.start_at == "2026-08-26T09:30:00"


def test_garbage_replies_are_refused():
    assert _extract_json("我不知道") is None
    assert _extract_json("") is None
    assert _extract_json("{not json}") is None

    hit = match("明天下午三点开会") or RuleHit()
    msg = message("明天下午三点开会")
    assert _to_candidate("嗯，我看看", msg, hit, NOW) is None


def test_an_unparseable_date_is_refused():
    hit = match("明天下午三点开会") or RuleHit()
    msg = message("明天下午三点开会")
    assert _to_candidate('{"date":"下周三","time":"15:00"}', msg, hit, NOW) is None


# --- pipeline integration ---------------------------------------------------


def test_the_model_confirmer_resolves_what_the_heuristic_refused(tmp_path):
    """A bare clock with no date is exactly the unresolved case."""
    ambiguous = "三点实验室开会"

    heuristic_only = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=_StaticClient([message(ambiguous)]),
        service=AgendaService(tmp_path / "a.db"),
        seen=SeenStore(tmp_path / "s1.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )
    before = heuristic_only.run_once()

    assert before.unresolved == 1, "expected the heuristic to refuse a bare clock"

    with_model = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=_StaticClient([message(ambiguous)]),
        service=AgendaService(tmp_path / "b.db"),
        seen=SeenStore(tmp_path / "s2.db"),
        confirmer=ModelConfirmer(
            completer=RecordingCompleter('{"date":"2026-08-25","time":"15:00","title":"实验室开会"}'),
            router=router_with(),
            now_factory=lambda: NOW,
        ),
    )
    after = with_model.run_once()

    assert after.unresolved == 0
    assert after.created, "the model-confirmed entry should have landed as pending"


class _StaticClient:
    def __init__(self, messages: list[ChatMessage]):
        self._messages = messages

    def fetch(self, talker: str, day=None):
        return self._messages

    def healthy(self):
        return True


# --- fallback ordering (cost + privacy discipline) --------------------------


def test_the_heuristic_result_never_reaches_the_model():
    """If rules can pin it down, the model is never called — no spend, no egress."""
    completer = RecordingCompleter()
    chained = FallbackConfirmer(
        primary=HeuristicConfirmer(now_factory=lambda: NOW),
        fallback=ModelConfirmer(
            completer=completer, router=router_with(), now_factory=lambda: NOW
        ),
    )

    hit = match("明天下午三点开会") or RuleHit()
    candidate = chained.confirm(message("明天下午三点开会"), hit)

    assert candidate is not None
    assert completer.prompts == [], "the model was called for a resolvable message"


def test_only_the_ambiguous_remainder_reaches_the_model():
    completer = RecordingCompleter('{"date":"2026-08-25","time":"15:00","title":"实验室开会"}')
    chained = FallbackConfirmer(
        primary=HeuristicConfirmer(now_factory=lambda: NOW),
        fallback=ModelConfirmer(
            completer=completer, router=router_with(), now_factory=lambda: NOW
        ),
    )

    hit = match("三点实验室开会") or RuleHit()
    candidate = chained.confirm(message("三点实验室开会"), hit)

    assert candidate is not None
    assert len(completer.prompts) == 1, "an unresolved message should reach the model exactly once"
    assert candidate.start_at == "2026-08-25T15:00:00"


def test_without_a_fallback_the_chain_matches_the_heuristic():
    chained = FallbackConfirmer(primary=HeuristicConfirmer(now_factory=lambda: NOW))

    hit = match("三点实验室开会") or RuleHit()
    assert chained.confirm(message("三点实验室开会"), hit) is None


# --- WP-EXTRACT-CONTEXT: who sent it, when, and the surrounding lines --------


def test_the_heuristic_anchors_relative_days_on_sent_time_not_the_wall_clock():
    """A message sent at 23:00 saying "明天下午三点" must land the NEXT day.

    The wall clock here has already rolled past midnight (00:05 on 9/11); only
    the message's own sent time (9/10) gives the right answer, 9/11.
    """
    confirmer = HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 11, 0, 5))
    hit = RuleHit(category="meeting", matched_keywords=["开会"])
    msg = ChatMessage(
        msg_id="m1",
        talker="班级群",
        sender="导师",
        sent_at="2026-09-10T23:00",
        content="明天下午三点开会",
    )

    candidate = confirmer.confirm(msg, hit)

    assert candidate is not None
    assert candidate.start_at == "2026-09-11T15:00:00"


def test_the_heuristic_refuses_a_date_without_a_clock():
    """No default clock: a date but no stated hour is handed to the model."""
    confirmer = HeuristicConfirmer(now_factory=lambda: NOW)
    hit = RuleHit(category="meeting", matched_keywords=["聚餐"])
    msg = ChatMessage(
        msg_id="m1",
        talker="班级群",
        sender="导师",
        sent_at="2026-08-25 10:00:00",
        content="周五晚上聚餐",
    )

    assert confirmer.confirm(msg, hit) is None


def test_the_prompt_carries_sender_sent_time_and_neighbours():
    completer = RecordingCompleter()
    confirmer = ModelConfirmer(completer=completer, router=router_with(), now_factory=lambda: NOW)
    hit = match("明天下午三点开会") or RuleHit()
    context = ConfirmContext(
        talker_name="拓扑测试群",
        prev_snippet="上面的人先说改时间",
        next_snippet="下面的人回复收到",
    )

    # message() -> sender="导师", sent_at="2026-08-25 10:00:00".
    confirmer.confirm(message("明天下午三点开会"), hit, context)

    prompt = completer.prompts[0]
    assert "导师" in prompt, "sender missing from the prompt"
    assert "2026-08-25 10:00:00" in prompt, "sent time missing from the prompt"
    assert "拓扑测试群" in prompt, "talker display name missing from the prompt"
    assert "上面的人先说改时间" in prompt and "上文" in prompt
    assert "下面的人回复收到" in prompt and "下文" in prompt


@pytest.mark.parametrize(
    "is_self,expected",
    [
        (True, "是本人发出"),
        (False, "不是本人发出"),
        (None, "未知"),
    ],
)
def test_is_self_is_reported_without_guessing(is_self, expected):
    completer = RecordingCompleter()
    confirmer = ModelConfirmer(completer=completer, router=router_with(), now_factory=lambda: NOW)
    hit = match("明天下午三点开会") or RuleHit()
    msg = ChatMessage(
        msg_id="m1",
        talker="班级群",
        sender="导师",
        sent_at="2026-08-25 10:00:00",
        content="明天下午三点开会",
        is_self=is_self,
    )

    confirmer.confirm(msg, hit)

    assert expected in completer.prompts[0]


def test_a_reply_saying_it_is_not_the_users_plan_is_refused():
    """Someone else's class schedule / forwarded plan must not be ingested."""
    completer = RecordingCompleter(
        '{"is_users_plan":false,"date":"2026-08-26","time":"15:00","title":"别人的课"}'
    )
    confirmer = ModelConfirmer(completer=completer, router=router_with(), now_factory=lambda: NOW)
    hit = match("明天下午三点开会") or RuleHit()

    assert confirmer.confirm(message("明天下午三点开会"), hit) is None


def test_an_end_time_becomes_the_candidate_end_at():
    completer = RecordingCompleter(
        '{"is_users_plan":true,"date":"2026-08-26","time":"15:00","end_time":"16:00","title":"组会"}'
    )
    confirmer = ModelConfirmer(completer=completer, router=router_with(), now_factory=lambda: NOW)
    hit = match("明天下午三点到四点开会") or RuleHit()

    candidate = confirmer.confirm(message("明天下午三点到四点开会"), hit)

    assert candidate is not None
    assert candidate.start_at == "2026-08-26T15:00:00"
    assert candidate.end_at == "2026-08-26T16:00:00"


def test_a_missing_end_time_is_not_invented():
    completer = RecordingCompleter('{"date":"2026-08-26","time":"15:00","title":"组会"}')
    confirmer = ModelConfirmer(completer=completer, router=router_with(), now_factory=lambda: NOW)
    hit = match("明天下午三点开会") or RuleHit()

    candidate = confirmer.confirm(message("明天下午三点开会"), hit)

    assert candidate is not None
    assert candidate.end_at is None
