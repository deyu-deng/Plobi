"""The quota spend ledger (WP-L2-COMPLETER-USAGE-LEDGER).

All tests inject a fake sender: verifying accounting must not itself spend
(治理线判据 ③). What is pinned is the three things the ledger exists to answer —
did we call it, which source, how many times — and that an unpriceable call is
never reported as free.
"""

from __future__ import annotations

import json

import pytest

from plobi.quota import usage_ledger
from plobi.quota.route import QuotaAwareCompleter
from plobi.quota.sources import QuotaSource


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "quota-spend.jsonl"
    monkeypatch.setattr(usage_ledger, "ledger_path", lambda: path)
    return path


def _rows(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _source(name="zhipu-air", kind="cheap_api"):
    return QuotaSource(name=name, kind=kind, base_url="http://x/v1", api_key="k", model="m")


class _Pool:
    """Hands out a scripted sequence of sources, recording failures like the real one."""

    def __init__(self, sources):
        self._sources = list(sources)
        self.failed = []

    def refresh_gateway(self):
        return None

    def resolve(self):
        return self._sources.pop(0) if self._sources else None

    def mark_failed(self, name):
        self.failed.append(name)


def test_one_successful_call_writes_exactly_one_named_row(ledger):
    pool = _Pool([_source()])
    reply = QuotaAwareCompleter(pool=pool, send=lambda prompt, source: "2")(
        "1 加 1?", route=None
    )

    assert reply == "2"
    rows = _rows(ledger)
    assert len(rows) == 1
    assert rows[0]["source"] == "zhipu-air"
    assert rows[0]["kind"] == "cheap_api"
    assert rows[0]["attempted"] is True and rows[0]["ok"] is True


def test_failover_writes_one_row_per_attempt(ledger):
    boom = OSError("connection refused")

    def send(prompt, source):
        if source.name == "workbuddy":
            raise boom
        return "2"

    pool = _Pool([_source("workbuddy", "aigw"), _source("zhipu-air", "cheap_api")])
    assert QuotaAwareCompleter(pool=pool, send=send)("go", route=None) == "2"

    rows = _rows(ledger)
    assert [(r["source"], r["ok"]) for r in rows] == [("workbuddy", False), ("zhipu-air", True)]
    assert rows[0]["error_class"] == "OSError"
    assert pool.failed == ["workbuddy"]


def test_usage_with_money_is_priced(ledger):
    row = usage_ledger.record(
        source="zhipu-air", kind="cheap_api", ok=True,
        usage={"total_tokens": 100, "total_cost_usd": 0.03},
        cost=usage_ledger.__dict__ and 0.03,
        cost_status=usage_ledger.COST_STATUS_PRICED,
    )
    assert row["cost"] == 0.03 and row["cost_status"] == "priced"


def test_usage_without_money_reports_null_and_never_zero(ledger):
    """R-073: an unpriced call must not read as a free call."""
    from plobi.quota.route import _cost_of, _cost_status

    usage = {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940}
    assert _cost_of(usage) is None
    assert _cost_status(usage) == usage_ledger.COST_STATUS_USAGE_NO_PRICE

    usage_ledger.record(source="zhipu-air", kind="cheap_api", ok=True, usage=usage,
                        cost=_cost_of(usage), cost_status=_cost_status(usage))
    row = _rows(ledger)[0]
    assert row["cost"] is None and row["usage"]["total_tokens"] == 940


def test_a_broken_ledger_cannot_break_the_spend(ledger, monkeypatch):
    def refuse():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(usage_ledger, "ledger_path", refuse)
    pool = _Pool([_source()])
    assert QuotaAwareCompleter(pool=pool, send=lambda prompt, source: "2")("go", route=None) == "2"


def test_summary_answers_did_we_call_which_source_how_many_times(ledger):
    usage_ledger.record(source="zhipu-air", kind="cheap_api", ok=True, usage={"total_tokens": 10})
    usage_ledger.record(source="zhipu-air", kind="cheap_api", ok=False, error_class="TimeoutError")
    usage_ledger.record(source="workbuddy", kind="aigw", ok=True, usage={"total_tokens": 5})

    summary = usage_ledger.summarize(_rows(ledger))
    assert summary["rows"] == 3
    assert summary["sources"] == ["workbuddy", "zhipu-air"]
    assert summary["per_source"]["zhipu-air"] == {"attempts": 2, "ok": 1, "failed": 1, "tokens": 10}
    assert summary["cost"] is None and summary["priced_rows"] == 0


def test_read_recent_is_empty_when_nothing_was_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ledger, "ledger_path", lambda: tmp_path / "absent.jsonl")
    assert usage_ledger.read_recent() == []


def test_a_stub_source_cannot_break_the_spend(ledger):
    """checkin's fake pool hands back a bare object(). A ledger that insists on a
    real QuotaSource would turn accounting into a new failure mode for callers."""
    pool = _Pool([object()])
    assert QuotaAwareCompleter(pool=pool, send=lambda prompt, source: "2")("go", route=None) == "2"
    assert _rows(ledger)[0]["source"] == "?"


def test_an_empty_reply_fails_over_instead_of_being_returned(ledger):
    """空货不是答案。返回 ``""`` 给调用方等于让它把「什么都没回来」当成「跑完了一轮」——
    今晚那份 0 块的计划就是这么判死的。"""
    pool = _Pool([_source("zhipu-air"), _source("workbuddy", kind="aigw"),
                  _source("workbuddy", kind="aigw")])
    replies = iter(["", "  ", "42"])

    reply = QuotaAwareCompleter(pool=pool, send=lambda prompt, source: next(replies))("go", route=None)

    assert reply == "42"
    assert pool.failed == ["zhipu-air", "workbuddy"]
    rows = _rows(ledger)
    assert [r["source"] for r in rows] == ["zhipu-air", "workbuddy", "workbuddy"]
    assert [r["ok"] for r in rows] == [False, False, True]
    assert [r["error_class"] for r in rows] == ["empty_reply", "empty_reply", ""]


def test_giving_up_always_leaves_a_named_row(ledger):
    """两条「一条都没发出去」的出口必须在账上看得见，否则成因只能拿 token 数反推。"""
    assert QuotaAwareCompleter(pool=_Pool([]), send=lambda prompt, source: "x")("go", route=None) is None
    rows = _rows(ledger)
    assert len(rows) == 1
    assert rows[0]["source"] == "quota-route"
    assert rows[0]["attempted"] is False
    assert rows[0]["error_class"] == "no_usable_source"
    assert rows[0]["cost_status"] == usage_ledger.COST_STATUS_NOT_ATTEMPTED


def test_all_legs_empty_leaves_the_attempt_and_the_give_up(ledger):
    pool = _Pool([_source()])
    assert QuotaAwareCompleter(pool=pool, send=lambda prompt, source: "   ")("go", route=None) is None

    rows = _rows(ledger)
    assert rows[0]["source"] == "zhipu-air" and rows[0]["ok"] is False
    assert rows[-1]["source"] == "quota-route"
    assert rows[-1]["error_class"] in ("no_usable_source", "attempts_exhausted")
