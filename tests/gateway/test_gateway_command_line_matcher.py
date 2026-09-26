"""Tests for the strict gateway command-line matcher.

Regression guard for the Windows ``plobi gateway restart`` silent-outage bug:
the previous loose substring match (``"... gateway" in cmdline``) false-matched
``gateway status``/``dashboard`` siblings and unrelated processes such as
``python -m tui_gateway``, which let ``restart()`` race a still-draining old
process and ``status``/``start`` report false positives.
"""

from __future__ import annotations

import pytest

from gateway.status import (
    looks_like_gateway_command_line as matches,
    looks_like_gateway_runtime_command_line as matches_runtime,
)


ACCEPT = [
    "pythonw.exe -m plobi_cli.main gateway run",
    r"C:\Users\me\plobi\venv\Scripts\pythonw.exe -m plobi_cli.main gateway run",
    "python -m plobi_cli.main --profile work gateway run",
    "python -m plobi_cli.main gateway run --replace",
    "python -m plobi_cli/main.py gateway run",
    "python gateway/run.py",
    "plobi-gateway.exe",
    "plobi gateway",          # bare `plobi gateway` defaults to run
    "plobi gateway run",
    # profile selector AFTER the `gateway` token (argv is profile-position
    # agnostic — _apply_profile_override strips --profile/-p anywhere)
    "plobi gateway --profile work run",
    "python -m plobi_cli.main gateway -p work run",
    "plobi gateway --profile=work run",
    # a profile literally NAMED "gateway"
    "plobi -p gateway gateway run",
    "python -m plobi_cli.main --profile gateway gateway run",
    # quoted Windows paths with spaces (shlex-aware tokenization)
    r'"C:\Program Files\Plobi\plobi-gateway.exe"',
    r'"C:\Program Files\Plobi\gateway\run.py" run',
    r'"C:\Program Files\Py\pythonw.exe" -m plobi_cli.main gateway run',
]

REJECT = [
    "python -m tui_gateway",                              # unrelated module
    "python -m plobi_cli.main gateway status",           # other subcommand
    "python -m plobi_cli.main gateway restart",
    "python -m plobi_cli.main gateway stop",
    "python -m plobi_cli.main --profile x dashboard",    # non-gateway subcommand
    "some random python -m mygateway thing",
    "",
    None,
]


@pytest.mark.parametrize("cmd", ACCEPT)
def test_accepts_real_gateway_run(cmd):
    assert matches(cmd) is True


@pytest.mark.parametrize("cmd", REJECT)
def test_rejects_non_gateway_run(cmd):
    assert matches(cmd) is False


def test_runtime_matcher_accepts_no_supervisor_restart_process():
    assert matches("python -m plobi_cli.main gateway restart") is False
    assert matches_runtime("python -m plobi_cli.main gateway restart") is True
    assert matches_runtime("python -m plobi_cli.main gateway status") is False
