"""Focused regressions for the Copilot ACP shim safety layer."""

from __future__ import annotations

import io
import json
import os
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.copilot_acp_client import CopilotACPClient


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()


class CopilotACPClientSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CopilotACPClient(acp_cwd="/tmp")

    def test_extracted_tool_calls_match_openai_sdk_shape(self) -> None:
        tool_response = (
            "I'll inspect that.\n"
            "<tool_call>"
            '{"id":"call_read","type":"function",'
            '"function":{"name":"read_file","arguments":"{\\"path\\":\\"README.md\\"}"}}'
            "</tool_call>"
        )

        with patch.object(self.client, "_run_prompt", return_value=(tool_response, "")):
            response = self.client._create_chat_completion(
                model="copilot-acp",
                messages=[{"role": "user", "content": "read README.md"}],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "read_file", "parameters": {}},
                    }
                ],
            )

        choice = response.choices[0]
        self.assertEqual(choice.finish_reason, "tool_calls")
        tool_call = choice.message.tool_calls[0]
        self.assertEqual(tool_call.id, "call_read")
        self.assertEqual(tool_call.function.name, "read_file")
        self.assertEqual(
            json.loads(tool_call.function.arguments),
            {"path": "README.md"},
        )
        self.assertEqual(dict(tool_call)["id"], "call_read")
        self.assertEqual(dict(tool_call.function)["name"], "read_file")
        self.assertEqual(choice.message.content, "I'll inspect that.")

    def test_stream_true_returns_iterable_text_chunks(self) -> None:
        with patch.object(self.client, "_run_prompt", return_value=("Hello from ACP", "")):
            stream = self.client._create_chat_completion(
                model="copilot-acp",
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
            )

        chunks = list(stream)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].choices[0].delta.content, "Hello from ACP")
        self.assertIsNone(chunks[0].choices[0].delta.tool_calls)
        self.assertEqual(chunks[0].choices[0].finish_reason, "stop")
        self.assertEqual(chunks[1].choices, [])
        self.assertEqual(chunks[1].usage.total_tokens, 0)

    def test_stream_true_preserves_tool_call_deltas(self) -> None:
        tool_response = (
            "<tool_call>"
            '{"id":"call_read","type":"function",'
            '"function":{"name":"read_file","arguments":"{\\"path\\":\\"README.md\\"}"}}'
            "</tool_call>"
        )

        with patch.object(self.client, "_run_prompt", return_value=(tool_response, "")):
            stream = self.client._create_chat_completion(
                model="copilot-acp",
                messages=[{"role": "user", "content": "read README.md"}],
                stream=True,
            )

        chunks = list(stream)
        delta = chunks[0].choices[0].delta
        self.assertIsNone(delta.content)
        self.assertEqual(chunks[0].choices[0].finish_reason, "tool_calls")
        self.assertEqual(len(delta.tool_calls), 1)
        tool_delta = delta.tool_calls[0]
        self.assertEqual(tool_delta.index, 0)
        self.assertEqual(tool_delta.id, "call_read")
        self.assertEqual(tool_delta.function.name, "read_file")
        self.assertEqual(
            json.loads(tool_delta.function.arguments),
            {"path": "README.md"},
        )
        self.assertEqual(chunks[1].choices, [])

    def test_timeout_object_is_coerced_for_streaming_requests(self) -> None:
        captured: dict[str, float] = {}

        def fake_run_prompt(prompt_text: str, *, timeout_seconds: float) -> tuple[str, str]:
            captured["timeout"] = timeout_seconds
            return "ok", ""

        timeout = type(
            "TimeoutLike",
            (),
            {"read": 12.0, "write": 5.0, "connect": 3.0, "pool": 1.0},
        )()

        with patch.object(self.client, "_run_prompt", side_effect=fake_run_prompt):
            list(
                self.client._create_chat_completion(
                    model="copilot-acp",
                    messages=[{"role": "user", "content": "hello"}],
                    timeout=timeout,
                    stream=True,
                )
            )

        self.assertEqual(captured["timeout"], 12.0)

    def _dispatch(self, message: dict, *, cwd: str) -> dict:
        process = _FakeProcess()
        handled = self.client._handle_server_message(
            message,
            process=process,
            cwd=cwd,
            text_parts=[],
            reasoning_parts=[],
        )
        self.assertTrue(handled)
        payload = process.stdin.getvalue().strip()
        self.assertTrue(payload)
        return json.loads(payload)

    def test_request_permission_is_not_auto_allowed(self) -> None:
        response = self._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/request_permission",
                "params": {},
            },
            cwd="/tmp",
        )

        outcome = (((response.get("result") or {}).get("outcome") or {}).get("outcome"))
        self.assertEqual(outcome, "cancelled")

    def test_read_text_file_blocks_internal_plobi_hub_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            blocked = home / ".plobi" / "skills" / ".hub" / "index-cache" / "entry.json"
            blocked.parent.mkdir(parents=True, exist_ok=True)
            blocked.write_text('{"token":"sk-test-secret-1234567890"}')

            with patch.dict(
                os.environ,
                {"HOME": str(home), "PLOBI_HOME": str(home / ".plobi")},
                clear=False,
            ):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "fs/read_text_file",
                        "params": {"path": str(blocked)},
                    },
                    cwd=str(home),
                )

        self.assertIn("error", response)

    def test_read_text_file_redacts_sensitive_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            secret_file = root / "config.env"
            secret_file.write_text("OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012")

            # agent.redact snapshots PLOBI_REDACT_SECRETS at import time into
            # _REDACT_ENABLED, so patching os.environ is a no-op. Flip the
            # module-level constant directly for the duration of the call.
            with patch("agent.redact._REDACT_ENABLED", True):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "fs/read_text_file",
                        "params": {"path": str(secret_file)},
                    },
                    cwd=str(root),
                )

        content = ((response.get("result") or {}).get("content") or "")
        self.assertNotIn("abc123def456", content)
        self.assertIn("OPENAI_API_KEY=", content)

    def test_write_text_file_reuses_write_denylist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            target = home / ".ssh" / "id_rsa"
            target.parent.mkdir(parents=True, exist_ok=True)

            with patch("agent.copilot_acp_client.is_write_denied", return_value=True, create=True):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "fs/write_text_file",
                        "params": {
                            "path": str(target),
                            "content": "fake-private-key",
                        },
                    },
                    cwd=str(home),
                )

        self.assertIn("error", response)
        self.assertFalse(target.exists())

    def test_write_text_file_respects_safe_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            safe_root = root / "workspace"
            safe_root.mkdir()
            outside = root / "outside.txt"

            with patch.dict(os.environ, {"PLOBI_WRITE_SAFE_ROOT": str(safe_root)}, clear=False):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "fs/write_text_file",
                        "params": {
                            "path": str(outside),
                            "content": "should-not-write",
                        },
                    },
                    cwd=str(root),
                )

        self.assertIn("error", response)
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()


# ── HOME env propagation tests (from PR #11285) ─────────────────────

from unittest.mock import patch as _patch
import pytest


def _make_home_client(tmp_path):
    return CopilotACPClient(
        api_key="copilot-acp",
        base_url="acp://copilot",
        acp_command="copilot",
        acp_args=["--acp", "--stdio"],
        acp_cwd=str(tmp_path),
    )


def _fake_popen_capture(captured):
    def _fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        raise FileNotFoundError("copilot not found")
    return _fake


def test_run_prompt_preserves_real_home_when_profile_home_available(monkeypatch, tmp_path):
    plobi_home = tmp_path / "plobi"
    (plobi_home / "home").mkdir(parents=True)
    real_home = tmp_path / "real-home"
    real_home.mkdir()

    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))

    captured = {}
    client = _make_home_client(tmp_path)

    with _patch("agent.copilot_acp_client.subprocess.Popen", side_effect=_fake_popen_capture(captured)):
        with pytest.raises(RuntimeError, match="Could not start Copilot ACP command"):
            client._run_prompt("hello", timeout_seconds=1)

    assert captured["kwargs"]["env"]["HOME"] == str(real_home)
    assert captured["kwargs"]["env"]["PLOBI_REAL_HOME"] == str(real_home)


def test_run_prompt_passes_home_when_parent_env_is_clean(monkeypatch, tmp_path):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("PLOBI_HOME", raising=False)

    captured = {}
    client = _make_home_client(tmp_path)

    with _patch("agent.copilot_acp_client.subprocess.Popen", side_effect=_fake_popen_capture(captured)):
        with pytest.raises(RuntimeError, match="Could not start Copilot ACP command"):
            client._run_prompt("hello", timeout_seconds=1)

    assert "env" in captured["kwargs"]
    assert captured["kwargs"]["env"]["HOME"]


# ── Windows spawn resolution + multi-CLI error wording ───────────────


def test_spawnable_resolves_a_bare_name_from_path(monkeypatch):
    """npm-style ``gemini.CMD`` is invisible to CreateProcess unless given in full.

    Measured 2026-10-25: ``shutil.which("gemini")`` finds the wrapper but
    ``Popen(["gemini", "--acp"])`` raises FileNotFoundError, while the absolute
    path starts fine.
    """
    from agent.copilot_acp_client import _spawnable

    monkeypatch.setattr(
        "agent.copilot_acp_client.shutil.which",
        lambda name: r"D:\Runtimes\Languages\Node\gemini.CMD" if name == "gemini" else None,
    )
    assert _spawnable("gemini") == r"D:\Runtimes\Languages\Node\gemini.CMD"


def test_spawnable_leaves_explicit_paths_and_missing_names_alone(monkeypatch):
    from agent.copilot_acp_client import _spawnable

    monkeypatch.setattr("agent.copilot_acp_client.shutil.which", lambda name: None)
    # Already an explicit path → nothing to look up.
    assert _spawnable("/usr/local/bin/opencode") == "/usr/local/bin/opencode"
    assert _spawnable(r"D:\Portables\cli\opencode\opencode.exe") == (
        r"D:\Portables\cli\opencode\opencode.exe"
    )
    # Not installed → keep the declared name so the caller's own error still names it.
    assert _spawnable("copilot") == "copilot"
    assert _spawnable("") == ""


class _ScriptedAcpProcess:
    """Minimal bidirectional stand-in for an ACP child process."""

    def __init__(self, replies):
        self._replies = replies
        self._out = queue.Queue()
        self.stdin = _LineWriter(self)
        self.stdout = _LineReader(self._out)
        self.stderr = _LineReader(queue.Queue())
        self.returncode = None
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class _LineWriter:
    def __init__(self, proc):
        self._proc = proc

    def write(self, text):
        try:
            msg = json.loads(text)
        except Exception:
            return
        reply = self._proc._replies.get(msg.get("method"))
        if reply is None:
            return
        body = {"jsonrpc": "2.0", "id": msg.get("id")}
        body.update(reply)
        self._proc._out.put(json.dumps(body) + "\n")

    def flush(self):
        pass


class _LineReader:
    def __init__(self, q):
        self._q = q

    def __iter__(self):
        return self

    def __next__(self):
        item = self._q.get()
        if item is None:
            raise StopIteration
        return item


def _reply_for(method_result=None, error=None):
    if error is not None:
        return {"error": error}
    return {"result": method_result}


def _patch_popen(proc):
    return _patch(
        "agent.copilot_acp_client.subprocess.Popen",
        return_value=proc,
    )


def test_session_new_auth_failure_names_the_cli_and_says_signed_in(monkeypatch, tmp_path):
    """A signed-out CLI answers -32000 on session/new, after a good handshake.

    Verified live against gemini 0.65.0 / qwen 0.26.0 / cline 3.0.73. The old
    wording said "Copilot ACP", which blamed the wrong product and told the user
    nothing about how to fix it.
    """
    monkeypatch.setattr("agent.copilot_acp_client.shutil.which", lambda name: None)
    proc = _ScriptedAcpProcess(
        {
            "initialize": _reply_for(
                {"protocolVersion": 1, "agentInfo": {"name": "qwen-code", "version": "0.26.0"}}
            ),
            "session/new": _reply_for(
                error={
                    "code": -32000,
                    "message": "Authentication required: Use Qwen Code CLI to authenticate first.",
                }
            ),
        }
    )
    client = CopilotACPClient(
        api_key="qwen-acp",
        base_url="acp://qwen",
        acp_command="qwen",
        acp_args=["--acp"],
        acp_cwd=str(tmp_path),
    )

    with _patch_popen(proc):
        with pytest.raises(RuntimeError) as excinfo:
            client._run_prompt("hi", timeout_seconds=3)

    text = str(excinfo.value)
    assert "qwen is not signed in" in text
    assert "own their own credentials" in text or "owns its own credentials" in text
    assert "Authentication required" in text
    assert "Copilot" not in text


def test_non_auth_error_names_the_cli_that_answered(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.copilot_acp_client.shutil.which", lambda name: None)
    proc = _ScriptedAcpProcess(
        {
            "initialize": _reply_for({"protocolVersion": 1}),
            "session/new": _reply_for(error={"code": -32601, "message": "Method not found"}),
        }
    )
    client = CopilotACPClient(
        api_key="gemini-acp",
        base_url="acp://gemini",
        acp_command="gemini",
        acp_args=["--acp"],
        acp_cwd=str(tmp_path),
    )

    with _patch_popen(proc):
        with pytest.raises(RuntimeError) as excinfo:
            client._run_prompt("hi", timeout_seconds=3)

    assert "ACP session/new failed (gemini)" in str(excinfo.value)


def test_model_candidates_keep_opencodes_measured_namespaced_shape():
    """OpenCode's agent is the one CLI proven to want providerID/modelID."""
    from agent.copilot_acp_client import _acp_namespace, _model_candidates

    ns = _acp_namespace("acp://opencode")
    assert ns == "opencode"
    assert _model_candidates("big-pickle", ns) == ["opencode/big-pickle", "big-pickle"]
    # An id that already carries a prefix goes through untouched.
    assert _model_candidates("opencode/big-pickle", ns) == ["opencode/big-pickle"]


def test_model_candidates_do_not_guess_a_prefix_for_the_other_clis():
    """A namespaced guess could be accepted-but-ignored, i.e. a silent wrong model."""
    from agent.copilot_acp_client import _acp_namespace, _model_candidates

    assert _model_candidates("gemini-3-pro", _acp_namespace("acp://gemini")) == ["gemini-3-pro"]
    assert _model_candidates("qwen3.5-plus", _acp_namespace("acp://qwen")) == ["qwen3.5-plus"]
    assert _model_candidates("deepseek-v4-flash", _acp_namespace("acp://cline")) == [
        "deepseek-v4-flash"
    ]
    # A real HTTP base_url has no ACP namespace at all.
    assert _acp_namespace("https://api.cline.bot/api/v1") == ""


def test_model_candidates_drop_an_empty_model_id():
    from agent.copilot_acp_client import _model_candidates

    assert _model_candidates("   ", "opencode") == []
