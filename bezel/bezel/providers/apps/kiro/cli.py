"""Kiro (AWS) — its own streaming API, the only door.

Measured on this machine 2026-10-31 with kiro-cli's own credential (read-only; we never
write its database): `runtime.us-east-1.kiro.dev` serves an AWS event-stream that brings
tool calls back into OUR loop, and `management.us-east-1.kiro.dev` lists the models. The
spawn path (`kiro-cli chat --no-interactive`) runs Kiro's own agent and returns only
prose, so this leg is pure HTTP: without a credential it raises `UpstreamError(reauth=True)`
and the card asks the owner to run Kiro's own login. Login stays Kiro's own command:
`kiro-cli login` prints the URL and waits.

Quota: Kiro exposes ``/usage`` only inside an interactive V3 session; there is no headless
usage command in 1.x, so this leg deliberately leaves ``acc.quota`` empty instead of
fabricating a percentage. Chat still consumes the same plan.

Install : npm i -g kiro-cli   (or curl -fsSL https://cli.kiro.dev/install | bash)
Verify  : kiro-cli --version ; kiro-cli chat --no-interactive "say hi"
"""
from __future__ import annotations

from ...base import Capabilities, Provider
from .http import KiroHttpMixin


class KiroCliProvider(KiroHttpMixin, Provider):
    name = "kiro_cli"
    display_name = "Kiro"
    binary = "kiro-cli"
    #: Verified from `kiro-cli login --help` on this machine 2026-10-26
    #: (`kiro-cli auth` does not exist — it errors "unrecognized subcommand").
    login_args = ("login",)
    #: Kiro's login prints the URL and waits, so it gets its own window.
    login_tty = True

    def login_plan(self) -> dict:
        """Kiro's own login command, in a window (it prints the URL and waits)."""
        return {
            "kind": "cli_login",
            "binary": self.binary,
            "argv": list(self.login_args),
            "timeout": 300.0,
            "tty": True,
            "name": self.display_name or self.name,
        }

    def capabilities(self) -> Capabilities:
        caps = self.default_capabilities.as_dict()
        # Tool calls come back natively on Kiro's own event-stream, so this leg is
        # tool-capable whenever it has a credential (no MCP registration involved).
        caps["tools"] = True
        return Capabilities(**caps)
