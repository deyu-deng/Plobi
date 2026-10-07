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

This file is NOT used by the gateway runtime; it lives only under aigw/tests/.

Prompt sentinels (checked in order):
    SIMULATE_EMPTY               exit 0, print nothing
    SIMULATE_SIGNED_OUT_STDERR   exit 0, auth hint on stderr, nothing on stdout
    SIMULATE_SIGNED_OUT_STDOUT   exit 0, auth hint on stdout
    SIMULATE_FAIL_EXIT           exit 3, message on stderr
    SIMULATE_STDERR_NOISE        normal answer + a large stderr blob
plus, for --help:
    env FAKE_CLI_HELP_NO_LIST=1  --help that advertises no model list at all
"""

from __future__ import annotations

import os
import sys

AUTH_HINT = "Authentication required. Please use /login command to sign in to your account"
HELP_TEXT = """Usage: fake [options] [command] [prompt]

  -p, --print                 Print response and exit
  --model <model>             Model for the current session. Please provide the
                              model ID. Currently supported: (alpha-1, beta-2, gamma-3)
"""


def _prompt_of(args: list[str]) -> str:
    if "-p" in args:
        i = args.index("-p")
        if i + 1 < len(args):
            return args[i + 1]
    return ""


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

    sys.stdout.write(f"FAKE_CLI<<{prompt}>>\n")


if __name__ == "__main__":
    main()
