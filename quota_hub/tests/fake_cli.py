"""Fake coding-agent CLI stand-in for tests.

Mimics the contract the CliProvider family relies on:

    fake_cli -p "<prompt>" [--model <model>]
    fake_cli --help

It echoes the prompt back (wrapped) so the spawn -> capture -> OpenAI-translation
pipeline is exercised without the real CLI or any vendor account, and it can also
reproduce the failure shapes a real CLI produces. Those shapes are not invented:
CodeBuddy Code exits **0** when it is signed out and puts the hint on stderr, and
the WorkBuddy-embedded build puts the same hint on stdout — both of which look
like success to a returncode check.

This file is NOT used by the gateway runtime; it lives only under quota_hub/tests/.

Prompt sentinels (checked in order):
    SIMULATE_EMPTY               exit 0, print nothing
    SIMULATE_SIGNED_OUT_STDERR   exit 0, auth hint on stderr, nothing on stdout
    SIMULATE_SIGNED_OUT_STDOUT   exit 0, auth hint on stdout
    SIMULATE_FAIL_EXIT           exit 3, message on stderr
    SIMULATE_STDERR_NOISE        normal answer + a large stderr blob
plus, for --help:
    env FAKE_CLI_HELP_NO_LIST=1  --help that advertises no model list at all

Output dialect (env FAKE_CLI_MODE), so one script can stand in for each new CLI:
    codex    JSONL: item.completed agent_message + turn.completed usage
    claude   one JSON object: {result, is_error, usage}
    cline    NDJSON: agent_event then run_result
    kiro     terminal frame lines around the answer (adapter must strip)
    opencode/""  plain text

The prompt may be positional (`codex exec "<p>"`, `opencode run "<p>"`), not only
behind -p; positional detection skips known flags and their values.
"""

from __future__ import annotations

import json
import os
import sys

AUTH_HINT = "Authentication required. Please use /login command to sign in to your account"
HELP_TEXT = """Usage: fake [options] [command] [prompt]

  -p, --print                 Print response and exit
  --model <model>             Model for the current session. Please provide the
                              model ID. Currently supported: (alpha-1, beta-2, gamma-3)
"""

# Flags whose next argv token is a value, not the positional prompt.
_VALUE_FLAGS = {
    "-m",
    "--model",
    "--sandbox",
    "--format",
    "--output-format",
    "--cwd",
    "--dir",
    "--agent",
    "-c",
}


def _prompt_of(args: list[str]) -> str:
    if "-p" in args:
        i = args.index("-p")
        if i + 1 < len(args):
            return args[i + 1]
    positional: list[str] = []
    skip = False
    for token in args:
        if skip:
            skip = False
            continue
        if token in _VALUE_FLAGS:
            skip = True
            continue
        if token.startswith("-"):
            continue
        positional.append(token)
    return positional[-1] if positional else ""


def _emit(mode: str, prompt: str) -> None:
    if mode == "codex":
        rows = [
            {"type": "thread.started", "thread_id": "fake"},
            {"type": "item.completed", "item": {"type": "reasoning", "text": "hidden"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": f"CODEX<<{prompt}>>"}},
            {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}},
        ]
        sys.stdout.write("\n".join(json.dumps(row) for row in rows) + "\n")
        return
    if mode == "claude":
        sys.stdout.write(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": f"CLAUDE<<{prompt}>>",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                }
            )
            + "\n"
        )
        return
    if mode == "cline":
        rows = [
            {"type": "agent_event", "event": {"text": "partial"}},
            {"type": "run_result", "text": f"CLINE<<{prompt}>>"},
        ]
        sys.stdout.write("\n".join(json.dumps(row) for row in rows) + "\n")
        return
    if mode == "kiro":
        sys.stdout.write(f"╭──────────────╮\n│ Kiro banner  │\n╰──────────────╯\nKIRO<<{prompt}>>\n")
        return
    sys.stdout.write(f"FAKE_CLI<<{prompt}>>\n")


def main() -> None:
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        # FAKE_CLI_HELP_NO_LIST mimics a CLI whose --help advertises no catalog at all.
        if os.environ.get("FAKE_CLI_HELP_NO_LIST"):
            sys.stdout.write(HELP_TEXT.replace("Currently supported: (alpha-1, beta-2, gamma-3)\n", ""))
        else:
            sys.stdout.write(HELP_TEXT)
        return

    prompt = _prompt_of(args)

    if "SIMULATE_SIGNED_OUT_STDERR" in prompt:
        sys.stderr.write(AUTH_HINT + "\n")
        return
    if "SIMULATE_SIGNED_OUT_STDOUT" in prompt:
        sys.stdout.write(AUTH_HINT + "\n")
        return
    if "SIMULATE_EMPTY" in prompt:
        return
    if "SIMULATE_FAIL_EXIT" in prompt:
        sys.stderr.write("fake cli exploded\n")
        sys.exit(3)
    if "SIMULATE_STDERR_NOISE" in prompt:
        sys.stderr.write("E" * 200_000 + "\n")

    _emit(os.environ.get("FAKE_CLI_MODE", ""), prompt)


if __name__ == "__main__":
    main()
