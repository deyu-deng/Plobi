"""New CLI legs: Codex / Claude Code / OpenCode / Kiro / Cline.

These pin the *contract with each CLI*, not the CLI itself:
  * argv shape (positional prompt, optional model pair)
  * JSON/NDJSON parsers (answer + usage; an error is an error)
  * native quota readers where a documented endpoint exists
  * clean auto-disable when the binary is absent

No real vendor account or network is used.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from quota_hub.providers.apps.claude_code import ClaudeCodeCliProvider
from quota_hub.providers.apps.claude_code.cli import _parse_claude_result, _parse_claude_usage
from quota_hub.providers.apps.cline import ClineCliProvider
from quota_hub.providers.apps.codex import CodexCliProvider
from quota_hub.providers.apps.codex.cli import (
    _parse_codex_events,
    _parse_codex_usage,
)
from quota_hub.providers.apps.kiro import KiroCliProvider
from quota_hub.providers.apps.opencode import OpenCodeCliProvider
from quota_hub.providers.apps.opencode.cli import _parse_opencode_usage
from quota_hub.providers.base import Account, Credential, UpstreamError


def _account(provider: str, token: str = "x") -> Account:
    return Account(id="a", provider=provider, cred=Credential(access_token=token))


def test_all_five_disable_cleanly_without_their_binary():
    classes = (
        (CodexCliProvider, "codex_cli"),
        (ClaudeCodeCliProvider, "claude_code_cli"),
        (OpenCodeCliProvider, "opencode_cli"),
        (KiroCliProvider, "kiro_cli"),
        (ClineCliProvider, "cline_cli"),
    )
    for cls, name in classes:
        prov = cls({"binary": "definitely-not-a-real-binary-xyz", "accounts": [{}]}, None)
        assert asyncio.run(prov.discover_accounts()) == [], name


def test_codex_invocation_is_positional_and_read_only():
    prov = CodexCliProvider({"binary": "codex"}, None)
    prov.binary_path = "codex"
    argv = prov.build_invocation(
        _account("codex_cli"),
        {"model": "codex_cli/gpt-5.4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert argv[:5] == ["codex", "exec", "--model", "gpt-5.4", "[user]\nhi"]
    assert "--json" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in argv


def test_claude_invocation_puts_model_before_print_mode():
    prov = ClaudeCodeCliProvider({"binary": "claude"}, None)
    prov.binary_path = "claude"
    argv = prov.build_invocation(
        _account("claude_code_cli"),
        {"model": "claude_code_cli/sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert argv == [
        "claude",
        "--model",
        "claude-sonnet-4-6",
        "-p",
        "[user]\nhi",
        "--output-format",
        "json",
    ]


def test_kiro_default_model_does_not_invent_a_model_flag():
    prov = KiroCliProvider({"binary": "kiro-cli"}, None)
    prov.binary_path = "kiro-cli"
    argv = prov.build_invocation(
        _account("kiro_cli"),
        {"model": "kiro_cli/default", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert "--model" not in argv, argv
    assert argv[-1] == "[user]\nhi"
    assert "--no-interactive" in argv and "--trust-all-tools" in argv


def test_cline_invocation_is_gone_because_acp_replaced_it():
    assert not hasattr(ClineCliProvider, "build_invocation"), (
        "Cline answers over ACP now; a `--json` argv shape would be a second, unused door"
    )


def test_codex_jsonl_takes_only_the_agent_message_and_usage():
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "secret"}}),
            json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "the answer"}}
            ),
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 7}}
            ),
        ]
    )
    text, usage = _parse_codex_events(stdout)
    assert text == "the answer"
    assert usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def test_codex_failed_turn_is_an_error_not_an_empty_answer():
    stdout = json.dumps({"type": "turn.failed", "error": {"message": "not logged in"}})
    try:
        _parse_codex_events(stdout)
        raise AssertionError("failed turn must raise")
    except UpstreamError as exc:
        assert exc.status == 502


def test_claude_json_result_and_error_shapes():
    ok = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "hello",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }
    )
    text, usage = _parse_claude_result(ok)
    assert text == "hello"
    assert usage["total_tokens"] == 5

    bad = json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": "no key"})
    try:
        _parse_claude_result(bad)
        raise AssertionError("is_error must raise")
    except UpstreamError as exc:
        assert exc.status == 502


def test_codex_usage_windows_are_normalized():
    now = 1_700_000_000
    groups = _parse_codex_usage(
        {
            "rate_limit": {
                "primary_window": {
                    "used_percent": 25,
                    "limit_window_seconds": 18_000,
                    "reset_after_seconds": 600,
                },
                "secondary_window": {
                    "used_percent": 60,
                    "limit_window_seconds": 604_800,
                    "resets_at": "2026-10-30T00:00:00Z",
                },
            }
        },
        now=now,
    )
    buckets = groups[0]["buckets"]
    assert buckets[0]["remaining_fraction"] == 0.75
    assert buckets[0]["window"] == "5h"
    assert buckets[0]["reset_time"].endswith("Z")
    assert buckets[1]["remaining_fraction"] == 0.4
    assert buckets[1]["window"] == "weekly"


def test_claude_usage_windows_are_normalized():
    groups = _parse_claude_usage(
        {
            "five_hour": {"utilization": 18.0, "resets_at": "2026-10-25T12:00:00Z"},
            "seven_day": {"utilization": 42.0, "resets_at": "2026-10-31T00:00:00Z"},
            "seven_day_opus": {"utilization": 4.0},
        }
    )
    buckets = groups[0]["buckets"]
    assert [b["bucket_id"] for b in buckets] == [
        "claude-five_hour",
        "claude-seven_day",
        "claude-seven_day_opus",
    ]
    assert buckets[0]["remaining_fraction"] == 0.82
    assert buckets[1]["remaining_fraction"] == 0.58


def test_opencode_usage_windows_are_normalized():
    groups = _parse_opencode_usage(
        {
            "usage": {
                "rolling": {"status": "ok", "percent": 10, "resetsAt": "2026-10-25T12:00:00Z"},
                "weekly": {"status": "ok", "percent": 50},
                "monthly": {"status": "ok", "percent": 75},
            }
        }
    )
    buckets = groups[0]["buckets"]
    assert [b["remaining_fraction"] for b in buckets] == [0.9, 0.5, 0.25]


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _Http:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get(self, url, *, headers=None, timeout=None):
        self.calls.append((url, headers or {}))
        return self.payload


def test_codex_quota_uses_the_local_auth_file(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"tokens": {"access_token": "tok", "account_id": "acct"}}), encoding="utf-8"
    )
    payload = {
        "rate_limit": {
            "primary_window": {"used_percent": 10, "limit_window_seconds": 18000},
        }
    }
    http = _Http(_Response(payload))
    prov = CodexCliProvider({"auth_file": str(auth)}, http)
    acc = _account("codex_cli")
    asyncio.run(prov.refresh_quota(acc))
    assert acc.quota["source"].startswith("codex:")
    assert acc.quota["groups"][0]["buckets"][0]["remaining_fraction"] == 0.9
    url, headers = http.calls[0]
    assert url.endswith("/backend-api/wham/usage")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["chatgpt-account-id"] == "acct"


def test_claude_quota_uses_the_local_credentials_file(tmp_path):
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok"}}), encoding="utf-8"
    )
    http = _Http(_Response({"five_hour": {"utilization": 30}}))
    prov = ClaudeCodeCliProvider({"credentials_file": str(creds)}, http)
    acc = _account("claude_code_cli")
    asyncio.run(prov.refresh_quota(acc))
    assert acc.quota["groups"][0]["buckets"][0]["remaining_fraction"] == 0.7
    url, headers = http.calls[0]
    assert url.endswith("/api/oauth/usage")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"


def test_opencode_quota_uses_the_go_key(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"opencode-go": {"key": "sk-go"}}), encoding="utf-8")
    http = _Http(_Response({"usage": {"rolling": {"percent": 40}}}))
    prov = OpenCodeCliProvider({"auth_file": str(auth)}, http)
    acc = _account("opencode_cli")
    asyncio.run(prov.refresh_quota(acc))
    assert acc.quota["groups"][0]["buckets"][0]["remaining_fraction"] == 0.6
    url, headers = http.calls[0]
    assert url.endswith("/zen/go/v1/usage")
    assert headers["Authorization"] == "Bearer sk-go"


def test_quota_failure_keeps_the_previous_snapshot(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "tok"}}), encoding="utf-8")
    prov = CodexCliProvider({"auth_file": str(auth)}, _Http(_Response({}, status=500)))
    acc = _account("codex_cli")
    acc.quota = {"groups": [{"display_name": "old", "buckets": []}]}
    asyncio.run(prov.refresh_quota(acc))
    assert acc.quota["groups"][0]["display_name"] == "old"
    assert "checked_at" not in acc.quota


def test_usage_fraction_tolerates_percent_and_fraction():
    from quota_hub.providers.apps.claude_code.cli import _used_fraction

    assert _used_fraction(25) == 0.25
    assert _used_fraction(0.25) == 0.25
    assert _used_fraction("n/a") is None
    assert _used_fraction(-5) == 0.0


# --- full spawn path through the fake CLI ---------------------------------

FAKE = str(Path(__file__).resolve().parent / "fake_cli.py")
PY = sys.executable


def _fake_cfg(mode: str, workspace: Path, **extra):
    cfg = {
        "binary": FAKE,
        "interpreter": PY,
        "workspace": str(workspace),
        "env": {"FAKE_CLI_MODE": mode},
        "accounts": [{"id": "fake-1"}],
    }
    cfg.update(extra)
    return cfg


def _spawn_chat(provider, model: str) -> tuple[str, dict]:
    asyncio.run(provider.discover_accounts())
    assert len(provider.accounts) == 1, "fake CLI should be discovered"
    return asyncio.run(
        provider.chat(
            provider.accounts[0],
            {"model": model, "messages": [{"role": "user", "content": "hello world"}]},
        )
    )


def test_codex_full_spawn_returns_the_agent_message_not_the_jsonl(tmp_path):
    prov = CodexCliProvider(_fake_cfg("codex", tmp_path), None)
    out = _spawn_chat(prov, "codex_cli/gpt-5.4")
    content = out["choices"][0]["message"]["content"]
    assert content == "CODEX<<[user]\nhello world>>"
    assert "thread.started" not in content
    assert out["usage"]["total_tokens"] == 8


def test_claude_full_spawn_reads_the_json_result(tmp_path):
    prov = ClaudeCodeCliProvider(_fake_cfg("claude", tmp_path), None)
    out = _spawn_chat(prov, "claude_code_cli/sonnet-4-6")
    assert out["choices"][0]["message"]["content"] == "CLAUDE<<[user]\nhello world>>"
    assert out["usage"]["total_tokens"] == 6


def test_kiro_full_spawn_strips_the_terminal_frame(tmp_path):
    prov = KiroCliProvider(_fake_cfg("kiro", tmp_path), None)
    out = _spawn_chat(prov, "kiro_cli/default")
    content = out["choices"][0]["message"]["content"]
    assert content == "KIRO<<[user]\nhello world>>"
    assert "banner" not in content


def test_opencode_full_spawn_returns_plain_text(tmp_path):
    prov = OpenCodeCliProvider(_fake_cfg("opencode", tmp_path), None)
    out = _spawn_chat(prov, "opencode_cli/default")
    assert out["choices"][0]["message"]["content"] == "FAKE_CLI<<[user]\nhello world>>"


def test_new_json_legs_stream_the_parsed_answer(tmp_path):
    prov = CodexCliProvider(_fake_cfg("codex", tmp_path), None)
    asyncio.run(prov.discover_accounts())
    chunks: list[dict] = []

    async def _drive():
        async for chunk in prov.chat_stream(
            prov.accounts[0],
            {"model": "codex_cli/gpt-5.4", "messages": [{"role": "user", "content": "hi"}]},
        ):
            chunks.append(chunk)

    asyncio.run(_drive())
    text = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if "content" in c["choices"][0]["delta"]
    )
    assert text == "CODEX<<[user]\nhi>>"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

