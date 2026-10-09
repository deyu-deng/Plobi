"""Cost readouts must distinguish "we cannot price this" from "this was free".

``sessions.estimated_cost_usd`` is ``NOT NULL DEFAULT 0``, so an unpriceable
route is stored as 0.0 — the exact same bytes as a session that cost nothing.
Every readout below therefore has to carry the status alongside the number and
yield ``None`` when there is no number, or a user asking "what did this chat
cost?" gets told it was free.

Invariants, not snapshots: no dollar figure is frozen here, so a legitimate
price-table edit cannot break this file.
"""

from __future__ import annotations

import pytest

starlette = pytest.importorskip("starlette.testclient", reason="fastapi/starlette not installed")
TestClient = starlette.TestClient

import plobi_state  # noqa: E402
from plobi_constants import get_plobi_home  # noqa: E402
from plobi_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN  # noqa: E402

UNPRICEABLE_MODEL = "FP16_Hermes_4.5-local"
PRICED_MODEL = "glm-5.3-flash"
PRICED_PROVIDER = "zai"
PRICED_BASE_URL = "https://api.z.ai/api/paas/v4"
# Shaped like the real complaint session: 634k of the prompt was cache hits.
PRICED_USAGE = {
    "input_tokens": 193_009,
    "output_tokens": 25_237,
    "cache_read_tokens": 634_496,
    "cache_write_tokens": 0,
    "reasoning_tokens": 20_163,
    "api_call_count": 21,
}


@pytest.fixture()
def client(monkeypatch, _isolate_plobi_home):
    # The z.ai route carries a base_url, so pricing would otherwise reach out to
    # /v1/models on the first estimate. Pin it to "endpoint knows nothing" so the
    # snapshot table is what these tests exercise.
    monkeypatch.setattr(
        "agent.usage_pricing.fetch_endpoint_model_metadata",
        lambda base_url, api_key=None: {},
    )
    db_path = get_plobi_home() / "state.db"
    monkeypatch.setattr(plobi_state, "DEFAULT_DB_PATH", db_path)
    with TestClient(app) as c:
        c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
        yield c


@pytest.fixture()
def db():
    session_db = plobi_state.SessionDB(db_path=plobi_state.DEFAULT_DB_PATH)
    try:
        yield session_db
    finally:
        session_db.close()


def _record_priced_session(db, session_id="priced"):
    """Write a session the way the agent does: price it, then store the result."""
    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

    result = estimate_usage_cost(
        PRICED_MODEL,
        CanonicalUsage(
            input_tokens=PRICED_USAGE["input_tokens"],
            output_tokens=PRICED_USAGE["output_tokens"],
            cache_read_tokens=PRICED_USAGE["cache_read_tokens"],
            cache_write_tokens=PRICED_USAGE["cache_write_tokens"],
            reasoning_tokens=PRICED_USAGE["reasoning_tokens"],
            request_count=PRICED_USAGE["api_call_count"],
        ),
        provider=PRICED_PROVIDER,
        base_url=PRICED_BASE_URL,
    )
    assert result.amount_usd is not None, "pricing regressed; fix the table first"
    db.create_session(session_id=session_id, source="cli", model=PRICED_MODEL)
    db.update_token_counts(
        session_id,
        model=PRICED_MODEL,
        billing_provider=PRICED_PROVIDER,
        billing_base_url=PRICED_BASE_URL,
        estimated_cost_usd=float(result.amount_usd),
        cost_status=result.status,
        cost_source=result.source,
        **{k: v for k, v in PRICED_USAGE.items()},
    )
    db._conn.commit()
    return float(result.amount_usd)


def _record_unpriced_session(db, session_id="unpriced"):
    """Write a session with usage but no pricing entry — status says unknown."""
    db.create_session(session_id=session_id, source="cli", model=UNPRICEABLE_MODEL)
    db.update_token_counts(
        session_id,
        model=UNPRICEABLE_MODEL,
        billing_provider=PRICED_PROVIDER,
        billing_base_url=PRICED_BASE_URL,
        input_tokens=50_000,
        output_tokens=5_000,
        cache_read_tokens=120_000,
        estimated_cost_usd=None,
        cost_status="unknown",
        cost_source="none",
        api_call_count=4,
    )
    db._conn.commit()


class TestPricedSessionIsComputable:
    """Invariant: a session on a model with a price yields a positive number
    through every readout, and cache-read tokens are part of that number."""

    def test_session_detail_reports_a_number(self, client, db):
        spent = _record_priced_session(db)
        data = client.get("/api/sessions/priced").json()

        assert data["estimated_cost_usd"] == pytest.approx(spent)
        assert data["estimated_cost_usd"] > 0
        assert data["cost_status"] == "estimated"
        # The readable exit for "what did this chat cost": tokens + money + status
        # all come off one readout, so nothing has to be guessed from the number.
        assert data["input_tokens"] == PRICED_USAGE["input_tokens"]
        assert data["cache_read_tokens"] == PRICED_USAGE["cache_read_tokens"]
        assert data["output_tokens"] == PRICED_USAGE["output_tokens"]

    def test_usage_analytics_reports_a_number(self, client, db):
        spent = _record_priced_session(db)
        data = client.get("/api/analytics/usage?days=7").json()

        assert data["totals"]["total_estimated_cost"] == pytest.approx(spent)
        assert data["totals"]["cost_status"] == "estimated"
        assert data["daily"][0]["estimated_cost"] == pytest.approx(spent)
        assert data["daily"][0]["cost_status"] == "estimated"
        row = next(m for m in data["by_model"] if m["model"] == PRICED_MODEL)
        assert row["estimated_cost"] == pytest.approx(spent)

    def test_models_analytics_reports_a_number(self, client, db):
        spent = _record_priced_session(db)
        data = client.get("/api/analytics/models?days=7").json()

        row = next(m for m in data["models"] if m["model"] == PRICED_MODEL)
        assert row["estimated_cost"] == pytest.approx(spent)
        assert row["cost_status"] == "estimated"
        assert data["totals"]["total_estimated_cost"] == pytest.approx(spent)
        assert data["totals"]["cost_status"] == "estimated"


class TestUnpricedSessionIsUnknown:
    """Invariant: usage we cannot price is ``None`` + ``unknown``, never 0."""

    def test_session_detail_does_not_report_zero(self, client, db):
        _record_unpriced_session(db)
        data = client.get("/api/sessions/unpriced").json()

        assert data["estimated_cost_usd"] is None
        assert data["cost_status"] == "unknown"
        # The usage itself stays readable — unknown cost is not unknown tokens.
        assert data["input_tokens"] == 50_000
        assert data["cache_read_tokens"] == 120_000

    def test_usage_analytics_does_not_report_zero(self, client, db):
        _record_unpriced_session(db)
        data = client.get("/api/analytics/usage?days=7").json()

        assert data["totals"]["total_estimated_cost"] is None
        assert data["totals"]["cost_status"] == "unknown"
        assert data["daily"][0]["estimated_cost"] is None
        assert data["daily"][0]["cost_status"] == "unknown"
        row = next(m for m in data["by_model"] if m["model"] == UNPRICEABLE_MODEL)
        assert row["estimated_cost"] is None
        assert row["cost_status"] == "unknown"

    def test_models_analytics_does_not_report_zero(self, client, db):
        _record_unpriced_session(db)
        data = client.get("/api/analytics/models?days=7").json()

        row = next(m for m in data["models"] if m["model"] == UNPRICEABLE_MODEL)
        assert row["estimated_cost"] is None
        assert row["cost_status"] == "unknown"
        assert data["totals"]["total_estimated_cost"] is None

    def test_session_list_does_not_report_zero(self, client, db):
        _record_unpriced_session(db)
        data = client.get("/api/sessions?limit=50").json()

        row = next(s for s in data["sessions"] if s["id"] == "unpriced")
        assert row["estimated_cost_usd"] is None
        assert row["cost_status"] == "unknown"


class TestMixedWindowIsPartial:
    """A window holding both priced and unpriced spend must keep the money it
    knows about and flag that the rest is unnamed."""

    def test_priced_plus_unpriced_is_partial_not_free(self, client, db):
        spent = _record_priced_session(db)
        _record_unpriced_session(db)
        data = client.get("/api/analytics/usage?days=7").json()

        assert data["totals"]["total_estimated_cost"] == pytest.approx(spent)
        assert data["totals"]["total_estimated_cost"] > 0
        assert data["totals"]["cost_status"] == "partial"

    def test_models_analytics_is_partial(self, client, db):
        spent = _record_priced_session(db)
        _record_unpriced_session(db)
        data = client.get("/api/analytics/models?days=7").json()

        priced_row = next(m for m in data["models"] if m["model"] == PRICED_MODEL)
        unpriced_row = next(m for m in data["models"] if m["model"] == UNPRICEABLE_MODEL)
        assert priced_row["estimated_cost"] == pytest.approx(spent)
        assert priced_row["cost_status"] == "estimated"
        assert unpriced_row["estimated_cost"] is None
        assert unpriced_row["cost_status"] == "unknown"
        assert data["totals"]["cost_status"] == "partial"


class TestNothingConsumedIsARealZero:
    """Zero tokens is a known total of $0 — a different claim from unknown."""

    def test_zero_usage_session_reports_zero_not_unknown(self, client, db):
        db.create_session(session_id="idle", source="cli", model=UNPRICEABLE_MODEL)
        db.update_token_counts("idle", input_tokens=0, output_tokens=0, api_call_count=0)
        db._conn.commit()

        data = client.get("/api/sessions/idle").json()
        assert data["estimated_cost_usd"] == 0.0
        assert data["cost_status"] == "none"

        totals = client.get("/api/analytics/usage?days=7").json()["totals"]
        assert totals["total_estimated_cost"] == 0.0
        assert totals["cost_status"] == "none"
