"""Cline CLI — an ACP leg, because that is the only door Cline opens to us.

Measured on this machine 2026-10-29 with cline 3.0.74 (`cline --acp`, JSON-RPC over
stdio): `initialize` answers with `agentInfo` and three `authMethods`, and `session/new`
refuses with `-32000 Authentication required: Call authenticate before starting a
session` until he has signed in. Once it accepts, the same answer carries the models that
session can run.

So this leg asks Cline three separate questions and takes its own words for each:
signed in? which models? does it honor `session/set_model`? — which is what
`providers/acp.py` does. The spawn form (`cline --json "<prompt>"`) stays out of it: it
flattens the conversation into one stdout blob, so a tool call has nowhere to go and the
picker would refuse the leg as `no_tools` even on the day he logs in.

Login is Cline's own command, in a window: `cline auth` prints
"interactive auth setup requires a TTY" when its stdout is a pipe (re-measured
2026-10-28), so the card opens a console instead of a pipe and keeps the exit code.
"""
from __future__ import annotations

from ...acp import AcpProvider


class ClineCliProvider(AcpProvider):
    name = "cline_cli"
    display_name = "Cline"
    binary = "cline"
    acp_args = ("--acp",)
    #: The picker shows `cline/<what Cline itself calls it>`.
    model_prefix = "cline"
    login_args = ("auth",)
    login_tty = True
