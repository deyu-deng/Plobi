"""Native Antigravity quota read (internalized from Antigravity-Manager).

No GUI and no local gateway: the account's own OAuth credential is used against
Google's ``retrieveUserQuotaSummary`` / ``fetchAvailableModels``. These tests
pin the parser and the failure policy (keep the last snapshot, never write zeros).
"""

from __future__ import annotations

import asyncio

from quota_hub.providers.apps.antigravity.api import (
    AntigravityProvider,
    _parse_model_quotas,
)
from quota_hub.providers.base import Account, Credential


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _Http:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def post(self, url, *, headers=None, json=None, timeout=None):
        self.calls.append((url, json))
        payload = self.payloads.pop(0) if self.payloads else {}
        if isinstance(payload, _Response):
            return payload
        return _Response(payload)


def _provider(http):
    return AntigravityProvider({"host": "https://daily-cloudcode-pa.googleapis.com", "accounts": []}, http)


def _account():
    return Account(
        id="acc-1",
        provider="antigravity",
        label="probe",
        cred=Credential(access_token="token", refresh_token="refresh"),
    )


def test_refresh_quota_reads_groups_and_model_percentages(monkeypatch):
    summary = {
        "groups": [
            {
                "displayName": "Gemini Models",
                "buckets": [
                    {"bucketId": "gemini-weekly", "window": "weekly",
                     "remainingFraction": 0.42, "resetTime": "2026-10-30T00:00:00Z"},
                    {"bucketId": "gemini-5h", "window": "5h",
                     "remainingFraction": 0.18, "resetTime": "2026-10-24T23:00:00Z"},
                ],
            }
        ]
    }
    models = {
        "models": {
            "gemini-3-pro-high": {
                "displayName": "Gemini 3 Pro",
                "quotaInfo": {"remainingFraction": 0.25, "resetTime": "2026-10-25T00:00:00Z"},
            }
        }
    }
    http = _Http([summary, models])
    provider = _provider(http)
    acc = _account()

    async def _fresh(_acc):
        return None

    async def _project(_acc):
        return "proj-1"

    monkeypatch.setattr(provider, "ensure_fresh", _fresh)
    monkeypatch.setattr(provider, "_project_id", _project)

    asyncio.run(provider.refresh_quota(acc))

    assert acc.quota["source"].startswith("antigravity:retrieveUserQuotaSummary")
    assert acc.quota["groups"][0]["display_name"] == "Gemini Models"
    assert [b["remaining_fraction"] for b in acc.quota["groups"][0]["buckets"]] == [0.42, 0.18]
    assert acc.quota["models"][0]["name"] == "gemini-3-pro-high"


def test_refresh_quota_keeps_the_previous_snapshot_on_failure(monkeypatch):
    http = _Http([_Response({}, status=500), _Response({}, status=500), _Response({}, status=500)])
    provider = _provider(http)
    acc = _account()
    acc.quota = {"groups": [{"display_name": "old", "buckets": []}]}

    async def _fresh(_acc):
        return None

    async def _project(_acc):
        return ""

    monkeypatch.setattr(provider, "ensure_fresh", _fresh)
    monkeypatch.setattr(provider, "_project_id", _project)

    asyncio.run(provider.refresh_quota(acc))

    assert acc.quota["groups"][0]["display_name"] == "old"
    assert "checked_at" not in acc.quota


def test_model_quota_parser_skips_rows_without_a_numeric_fraction():
    rows = _parse_model_quotas(
        {
            "models": {
                "gemini-3-flash": {"quotaInfo": {"remainingFraction": 0.5}},
                "gemini-no-quota": {"displayName": "n/a"},
                "gemini-bad": {"quotaInfo": {"remainingFraction": "unknown"}},
                "gemini-out": {"quotaInfo": {"remainingFraction": 1.7}},
            }
        }
    )

    assert [row["name"] for row in rows] == ["gemini-3-flash"]
