"""裁定 215 #35：webhook 外部事件默认 observe，显式 auto／assessor 放行才起 Agent 轮。"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter, _INSECURE_NO_AUTH
from plobi.delegation.tickets import OBSERVED, TicketLedger


@pytest.fixture
def ticket_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "delegation_tickets.db"))
    return TicketLedger()


def _make_adapter(**extra_kw) -> WebhookAdapter:
    extra = {
        "host": "127.0.0.1",
        "port": 0,
        "routes": {
            "ci-event": {
                "secret": _INSECURE_NO_AUTH,
                "prompt": "CI said {payload.msg}",
            }
        },
    }
    extra.update(extra_kw)
    return WebhookAdapter(PlatformConfig(enabled=True, extra=extra))


def _app(adapter: WebhookAdapter) -> web.Application:
    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    return app


async def _post(adapter, delivery: str = "d-1"):
    app = _app(adapter)
    body = json.dumps({"payload": {"msg": "build failed"}}).encode()
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            "/webhooks/ci-event",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": delivery,
            },
        )
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_default_observe_only_records_a_ticket(ticket_db):
    adapter = _make_adapter()
    called: list[object] = []

    async def _capture(event):
        called.append(event)

    adapter.handle_message = _capture
    status, data = await _post(adapter)
    await asyncio.sleep(0.05)

    assert status == 202
    assert data["status"] == "observed"
    assert data["reason"] == "observe by default"
    assert called == []

    ticket = ticket_db.get(data["ticket_id"])
    assert ticket is not None
    assert ticket.status == OBSERVED
    assert ticket.leg == "trigger"
    assert ticket.triggered_by == "webhook:ci-event"
    assert ticket.refs["delivery_id"] == "d-1"
    assert ticket.scope["allowed_capabilities"] == []


@pytest.mark.asyncio
async def test_explicit_auto_keeps_the_old_agent_path(ticket_db):
    adapter = _make_adapter(trigger_policy="auto")
    called: list[object] = []

    async def _capture(event):
        called.append(event)

    adapter.handle_message = _capture
    status, data = await _post(adapter)
    await asyncio.sleep(0.05)

    assert status == 202
    assert data["status"] == "accepted"
    assert len(called) == 1
    assert ticket_db.list_tickets(status=OBSERVED) == []


@pytest.mark.asyncio
async def test_assess_without_assessor_degrades_to_observe(ticket_db):
    adapter = _make_adapter(trigger_policy="assess")
    called: list[object] = []

    async def _capture(event):
        called.append(event)

    adapter.handle_message = _capture
    status, data = await _post(adapter)
    await asyncio.sleep(0.05)

    assert status == 202
    assert data["status"] == "observed"
    assert data["reason"] == "no assessor configured"
    assert called == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict, expected_status, expected_agent_calls",
    [(True, "accepted", 1), ("auto", "accepted", 1), (False, "observed", 0)],
)
async def test_assessor_verdict_controls_execution(
    ticket_db, verdict, expected_status, expected_agent_calls
):
    adapter = _make_adapter(trigger_policy="assess")
    seen: list[dict] = []

    def _assess(**kwargs):
        seen.append(kwargs)
        return verdict

    adapter.set_trigger_assessor(_assess)
    called: list[object] = []

    async def _capture(event):
        called.append(event)

    adapter.handle_message = _capture
    status, data = await _post(adapter)
    await asyncio.sleep(0.05)

    assert status == 202
    assert data["status"] == expected_status
    assert len(called) == expected_agent_calls
    assert seen and seen[0]["route"] == "ci-event"


@pytest.mark.asyncio
async def test_human_activation_makes_the_ticket_executable(ticket_db):
    adapter = _make_adapter()
    status, data = await _post(adapter)
    assert status == 202

    assert ticket_db.is_executable(data["ticket_id"]) is False
    assert ticket_db.activate(data["ticket_id"]) is True
    assert ticket_db.is_executable(data["ticket_id"]) is True
    assert ticket_db.revoke(data["ticket_id"], reason="人喊停") is True
    assert ticket_db.is_executable(data["ticket_id"]) is False
