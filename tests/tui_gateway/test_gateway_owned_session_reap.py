"""Tests for #60609: the TUI backend must not end gateway-owned sessions.

``_finalize_session`` (and thus the ws-orphan reaper / session.close paths
that funnel into it) marks the session ended in state.db.  For sessions the
messaging gateway owns (telegram, discord, ...), that write creates the
Groundhog Day routing loop described in #60609 — the gateway self-heal
drops the ended-but-routed entry, recovers hours-old parent context, and
loops.  The TUI is only a viewer of those sessions.
"""

from unittest.mock import MagicMock, patch

from tui_gateway.server import _finalize_session, _is_gateway_owned_source


class TestIsGatewayOwnedSource:
    def test_builtin_gateway_platforms_are_owned(self):
        for src in ("telegram", "discord", "whatsapp", "slack", "signal",
                    "matrix", "mattermost", "bluebubbles", "sms", "email"):
            assert _is_gateway_owned_source(src) is True, src

    def test_case_and_whitespace_normalized(self):
        assert _is_gateway_owned_source(" Telegram ") is True

    def test_tui_owned_sources_are_not(self):
        for src in ("tui", "cli", "webui", "desktop", "cron", "subagent",
                    "test", "acp", ""):
            assert _is_gateway_owned_source(src) is False, src

    def test_local_and_server_endpoints_are_not(self):
        # Platform enum members, but their sessions aren't owned by a remote
        # chat surface — reaping them keeps /resume clean.
        for src in ("local", "webhook", "api_server", "msgraph_webhook"):
            assert _is_gateway_owned_source(src) is False, src

    def test_arbitrary_strings_are_not(self):
        assert _is_gateway_owned_source("hermesbench-task-xyz") is False
        assert _is_gateway_owned_source(None) is False


def _make_session(session_id="sess_1"):
    agent = MagicMock()
    agent.session_id = session_id
    return {
        "agent": agent,
        "history": [{"role": "user", "content": "x"}],
        "history_lock": None,
        "session_key": session_id,
    }


class TestFinalizeSkipsGatewaySessions:
    @patch("tui_gateway.server._get_db")
    def test_gateway_session_not_ended(self, mock_get_db):
        db = MagicMock()
        db.get_session.return_value = {"id": "sess_1", "source": "telegram"}
        mock_get_db.return_value = db

        _finalize_session(_make_session(), end_reason="ws_orphan_reap")

        db.end_session.assert_not_called()

    @patch("tui_gateway.server._get_db")
    def test_tui_session_still_ended(self, mock_get_db):
        db = MagicMock()
        db.get_session.return_value = {"id": "sess_1", "source": "tui"}
        mock_get_db.return_value = db

        _finalize_session(_make_session(), end_reason="ws_orphan_reap")

        db.end_session.assert_called_once_with("sess_1", "ws_orphan_reap")

    @patch("tui_gateway.server._get_db")
    def test_missing_row_still_ended(self, mock_get_db):
        """A session with no state.db row can't be gateway-owned — keep the
        pre-existing reap behavior."""
        db = MagicMock()
        db.get_session.return_value = None
        mock_get_db.return_value = db

        _finalize_session(_make_session(), end_reason="tui_close")

        db.end_session.assert_called_once_with("sess_1", "tui_close")


class TestOrphanReapIsReversible:
    """A ``ws_orphan_reap`` must stay a *liveness* marker, never a tombstone.

    Reproduces the desktop symptom: quit and relaunch the app, the renderer's
    websocket drops, ``_schedule_ws_orphan_reap`` closes the session after the
    grace window (``ended_at`` + ``end_reason=ws_orphan_reap``), and the
    conversation is still on screen with its 446 messages. The next send then
    hit a binding the gateway no longer has.

    The relationship these tests pin — and the reason the fix is *not* "stop
    reaping":
      1. the reap legitimately drops the LIVE binding, so ``prompt.submit``
         answers 4001 ``session not found``. That rejection is actionable, not
         terminal: the stored row is still there and still holds every message.
      2. ``session.resume`` heals it — it goes through
         ``SessionDB.reopen_session``, which clears ``ended_at``/``end_reason``.
         So the client recovers by resuming, and the reaper keeps releasing the
         agent, its slash worker and its active-session slot for conversations
         nobody is connected to.
    """

    def test_reaped_row_survives_with_its_messages(self, tmp_path):
        from plobi_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        db.create_session("reaped", source="desktop")

        for i in range(3):
            db.append_message("reaped", role="user", content=f"msg {i}")

        db.end_session("reaped", "ws_orphan_reap")

        row = db.get_session("reaped")
        assert row is not None, "the reap must not delete the row"
        assert row["end_reason"] == "ws_orphan_reap"
        assert row["ended_at"] is not None
        assert row["message_count"] == 3, "the reap must not touch the transcript"

    def test_reopen_clears_the_reap_marker_so_resume_can_rebind(self, tmp_path):
        """What ``session.resume`` calls (``db.reopen_session``) reverses the reap."""
        from plobi_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        db.create_session("reaped", source="desktop")
        db.append_message("reaped", role="user", content="still here")
        db.end_session("reaped", "ws_orphan_reap")

        db.reopen_session("reaped")

        row = db.get_session("reaped")
        assert row["ended_at"] is None
        assert row["end_reason"] is None
        assert row["message_count"] == 1

    def test_a_reaped_id_stays_resumable_as_itself(self, tmp_path):
        """The client resumes the id it is showing; the chain walk must not
        bounce it to a stale sibling and strand that conversation."""
        from plobi_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        db.create_session("reaped", source="desktop")
        db.append_message("reaped", role="user", content="hello")
        db.end_session("reaped", "ws_orphan_reap")

        assert db.resolve_resume_session_id("reaped") == "reaped"

    def test_submit_after_reap_returns_the_actionable_signal(self):
        """``prompt.submit``/attachments resolve the live binding only, and the
        rejection they raise carries exactly the code+message the desktop keys
        its recovery on (``isSessionNotFoundError`` in
        apps/desktop/src/app/session/hooks/use-prompt-actions/utils.ts)."""
        from tui_gateway.server import _sess_nowait

        session, err = _sess_nowait({"session_id": "no-such-live-id"}, "rid")

        assert session is None
        assert err["error"]["code"] == 4001
        assert err["error"]["message"] == "session not found"
