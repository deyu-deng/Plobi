"""Offline tests for the seam-B CodeBuddy ACP client (no live CLI, no quota).

These pin the security-relevant pure logic. The end-to-end ACP turn is NOT
covered here — it needs a gated live run — so nothing below should be read as
proof the full loop works; it only proves the decision helpers behave.
"""

from __future__ import annotations

import logging

logging.disable(logging.CRITICAL)

from agent.codebuddy_acp_client import (
    CodeBuddyACPClient,
    _mcp_servers_entry,
    ask_approval,
    flatten_messages,
    map_permission_outcome,
)

ALLOW_OPTS = [
    {"optionId": "allow_once", "kind": "allow_once"},
    {"optionId": "allow_session", "kind": "allow_always"},
    {"optionId": "allow_always", "kind": "allow_always"},
    {"optionId": "deny", "kind": "reject_once"},
]


def test_deny_maps_to_cancelled_never_selected():
    out = map_permission_outcome(ALLOW_OPTS, "deny")
    assert out == {"outcome": {"outcome": "cancelled"}}


def test_once_prefers_allow_once():
    out = map_permission_outcome(ALLOW_OPTS, "once")
    assert out == {"outcome": {"outcome": "selected", "optionId": "allow_once"}}


def test_always_prefers_allow_always_then_session():
    assert map_permission_outcome(ALLOW_OPTS, "always")["outcome"]["optionId"] == "allow_always"
    only_session = [{"optionId": "allow_session", "kind": "allow_always"}]
    assert map_permission_outcome(only_session, "always")["outcome"]["optionId"] == "allow_session"


def test_never_invents_an_option_that_was_not_offered():
    # agent offered only a deny option -> deny, even though decision was 'once'
    only_deny = [{"optionId": "deny", "kind": "reject_once"}]
    assert map_permission_outcome(only_deny, "once") == {"outcome": {"outcome": "cancelled"}}


def test_falls_back_to_any_allow_kind():
    odd = [{"optionId": "weird_ok", "kind": "allow_once"}]
    assert map_permission_outcome(odd, "once")["outcome"]["optionId"] == "weird_ok"


def test_approval_fail_closed_when_no_callback():
    # THE safety invariant: absent approver => deny, never auto-allow.
    assert ask_approval(None, "terminal", {"command": "rm -rf /"}) == "deny"


def test_approval_fail_closed_when_callback_raises():
    def boom(*a, **k):
        raise RuntimeError("approver died")
    assert ask_approval(boom, "write_file", {"path": "x"}) == "deny"


def test_approval_passes_through_callback_decision():
    assert ask_approval(lambda *a, **k: "always", "terminal", {}) == "always"


def test_flatten_messages_handles_list_and_roles():
    got = flatten_messages([
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "bogus", "content": ""},
    ])
    assert got.startswith("system:\nbe terse")
    assert "user:\nhi" in got
    assert "bogus" not in got  # empty content dropped


def test_mcp_servers_entry_is_list_env_and_targets_plobi_server():
    entry = _mcp_servers_entry()
    assert isinstance(entry, list) and len(entry) == 1
    srv = entry[0]
    assert srv["name"] == "plobi"
    assert srv["args"][-1] == "agent.transports.plobi_quota_mcp_server"
    # The bug that hung session/new: env MUST be a list of {name,value}, not a dict.
    assert isinstance(srv["env"], list)
    assert all({"name", "value"} == set(kv) for kv in srv["env"])


def test_mcp_servers_entry_pins_cwd_and_pythonpath_to_repo_root():
    # Measured 2026-10-08: without cwd + PYTHONPATH=repo root, CodeBuddy spawns the
    # server in a temp dir, `python -m agent.*` can't import the package, and the
    # agent sees ZERO plobi tools ("isn't available"). This pins that fix.
    import os
    from pathlib import Path

    srv = _mcp_servers_entry()[0]
    repo_root = str(Path(__file__).resolve().parents[2])
    assert srv["cwd"] == repo_root
    env = {kv["name"]: kv["value"] for kv in srv["env"]}
    assert env.get("PYTHONPATH", "").split(os.pathsep)[0] == repo_root


def test_client_defaults_are_codebuddy_marker_and_safe_facade():
    # Constructing must not spawn anything; default base_url is the codebuddy ACP marker.
    c = CodeBuddyACPClient(approval_callback=None)
    assert c.base_url == "acp://codebuddy"
    assert callable(c.chat.completions.create)
