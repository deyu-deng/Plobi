"""Two kinds of "not now" must be told apart before a model reaches the picker.

Measured 2026-11-08 on this machine: the hub reports one leg whose models are all
`unselectable_reason=quota_exhausted` with a `retry_after_seconds` — a bucket that
refills on its own — beside another whose models are dead for good (an expired
credential). The first belongs on screen (greyed, with the time left on it); the
second must not be offered at all (user: 「我配置不了就不应该出现在这个模型框里面呀」).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import plobi_cli.models as models

_ROWS = [
    {"id": "workbuddy/hy3", "selectable": True},
    {
        "id": "opencode_api/big-pickle",
        "selectable": False,
        "unselectable_reason": "quota_exhausted",
        "retry_after_seconds": 1754,
        "available_at": "2026-11-08T23:13:02",
    },
    {
        "id": "opencode_api/flint-40b-free",
        "selectable": False,
        "unselectable_reason": "quota_exhausted",
        "retry_after_seconds": 900,
        "available_at": "2026-11-08T23:30:00",
    },
    {
        "id": "kiro_cli/claude-sonnet-4.5",
        "selectable": False,
        "unselectable_reason": "credential_expired",
    },
    {
        "id": "kiro_cli/claude-haiku-4.5",
        "selectable": False,
        "unselectable_reason": "no_tools",
    },
]


class _Hub(BaseHTTPRequestHandler):
    """A stand-in hub that serves the full catalogue.

    The real hub filters by `?selectable=1`; this stand-in honours that too, so
    the two questions the picker asks stay apart.
    """

    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append(self.path)
        assert self.headers.get("Authorization") == "Bearer test-key"
        rows = _ROWS
        if "selectable=1" in self.path:
            rows = [row for row in _ROWS if row.get("selectable")]
        body = json.dumps({"object": "list", "data": rows}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence stderr noise during tests
        return


def _serve():
    server = HTTPServer(("127.0.0.1", 0), _Hub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_model_that_is_only_waiting_is_offered_beside_the_usable_ones():
    server = _serve()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        ids = models.hub_offered_model_ids("test-key", base)
    finally:
        server.shutdown()

    # The waiting ones stay; the ones the user has to fix do not.
    assert ids == [
        "workbuddy/hy3",
        "opencode_api/big-pickle",
        "opencode_api/flint-40b-free",
    ]


def test_the_waiting_models_carry_the_reason_and_the_time_left():
    server = _serve()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        waiting = models.hub_waiting_models("test-key", base)
    finally:
        server.shutdown()

    assert waiting == {
        "opencode_api/big-pickle": {
            "reason": "quota_exhausted",
            "retry_after_seconds": 1754,
            "available_at": "2026-11-08T23:13:02",
        },
        "opencode_api/flint-40b-free": {
            "reason": "quota_exhausted",
            "retry_after_seconds": 900,
            "available_at": "2026-11-08T23:30:00",
        },
    }


def test_a_model_the_user_can_fix_is_not_waiting():
    # A dead credential does not refill by itself — it must not be dressed up as
    # "wait a bit", and it must not be offered either.
    assert models.hub_row_is_waiting({"unselectable_reason": "credential_expired"}) is False
    assert models.hub_row_is_waiting({"unselectable_reason": "no_tools"}) is False
    assert models.hub_row_is_waiting({"unselectable_reason": "quota_exhausted"}) is True
    assert models.hub_row_is_waiting({"selectable": True}) is False


def test_an_unreachable_hub_answers_none_not_a_list():
    # "we could not ask" and "the hub has nothing" are different claims; the caller
    # decides what to do with the first one, so it must be distinguishable.
    assert models.hub_offered_model_ids("k", "http://127.0.0.1:1/v1", timeout=1.0) is None
    # The waiting map has no such distinction to make: an unanswered hub has no
    # waiting models, and saying so is better than inventing a reason.
    assert models.hub_waiting_models("k", "http://127.0.0.1:1/v1", timeout=1.0) == {}


def test_the_global_model_list_never_carries_a_waiting_model():
    # `provider_model_ids` feeds the CLI/TUI picker, the switch validation and the
    # settings page — none of which know how to grey a row out. A model that can
    # only be waited for must reach the chat picker through the payload's
    # `capabilities[model].unavailable` instead, and not through this list.
    server = _serve()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        ids = models.hub_selectable_model_ids("test-key", base)
    finally:
        server.shutdown()

    assert "opencode_api/big-pickle" not in ids
