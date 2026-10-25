"""Antigravity (Google) provider via the official `agy` CLI — spawn-based.

Compliant route: instead of reusing the reverse-engineered Cloud Code adapter
(antigravity.py, which needs a captured OAuth token), we spawn the user's own
`agy` binary in non-interactive mode:

    agy -p "<prompt>" [--model gemini-3-pro]

`agy` consumes the user's own Google quota and handles its own auth (OAuth on
first run). We only flatten the conversation into the prompt and translate the
stdout back into an OpenAI ChatCompletion. Zero token scraping.

Install :  curl -fsSL https://antigravity.google/cli/install.sh | bash
Verify  :  agy --version   (binary on PATH; first run does the OAuth login)

VERIFY (one-time, on your machine): confirm `agy -p "say hi"` prints the raw
reply to STDOUT (no TTY banner, no pager). If `agy` needs a different flag to
emit a non-interactive reply (e.g. `--print` / `--output text`), override
`prompt_flag` / add `extra_args` in config. The model flag is only sent when a
model is resolved (see CliProvider.build_invocation), so a bare `agy -p` works
too.

If `agy` is not on PATH the provider auto-disables (0 accounts) — see
test_cli_provider.py for the fake-CLI verification harness.
"""

from __future__ import annotations

from ...cli import CliProvider


class AntigravityCliProvider(CliProvider):
    name = "antigravity_cli"
    binary = "agy"
    prompt_flag = "-p"
    model_flag = "--model"
    timeout = 180.0
    served_models = (
        "antigravity_cli/gemini-3-pro",
        "antigravity_cli/gemini-3-flash",
        "antigravity_cli/claude-sonnet-4-6",
    )
    model_map = {
        "antigravity_cli/gemini-3-pro": "gemini-3-pro",
        "antigravity_cli/gemini-3-flash": "gemini-3-flash",
        "antigravity_cli/claude-sonnet-4-6": "claude-sonnet-4-6",
    }
