"""Cline — its own OpenAI-compatible door first, the ACP session as the floor.

Measured on this machine 2026-10-29 with cline 3.0.74 (`cline --acp`, JSON-RPC over
stdio): `initialize` answers with `agentInfo` and three `authMethods`, and `session/new`
refuses with `-32000 Authentication required: Call authenticate before starting a
session` until he has signed in. Once it accepts, the same answer carries the models that
session can run. That session runs *its* tools and returns a finished answer, so a tool
call has nowhere to go in our loop.

Re-measured 2026-10-31: `api.cline.bot/api/v1` is a real OpenAI-shaped door for this
account (439 ids, `finish_reason="tool_calls"` back in 0.7-7.1 s), so `http.py` serves the
turn whenever a credential exists; `providers/acp.py` stays behind it for the case where
there is none. Login remains Cline's own command, in a window: `cline auth` prints
"interactive auth setup requires a TTY" when its stdout is a pipe (re-measured
2026-10-28), so the card opens a console instead of a pipe and keeps the exit code.
"""
from __future__ import annotations

from ...acp import AcpProvider
from .http import ClineHttpMixin


class ClineCliProvider(ClineHttpMixin, AcpProvider):
    name = "cline_cli"
    display_name = "Cline"
    binary = "cline"
    acp_args = ("--acp",)
    #: The picker shows `cline/<what Cline itself calls it>`.
    model_prefix = "cline"
    login_args = ("auth",)
    login_tty = True
