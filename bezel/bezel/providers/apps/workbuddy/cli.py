"""Workbuddy provider via a locally-installed Workbuddy CLI (spawn-based).

Compliant route (opendesign-style): if your Workbuddy build ships a CLI (or you
wrap the desktop app in a small CLI shim), this provider spawns it as a
subprocess and translates its stdout into an OpenAI ChatCompletion — the same
mechanism as AntigravityCliProvider. Every CLI-specific detail (binary, prompt
flag, model flag, served models) comes from config, so this one class covers
any CLI-shaped Workbuddy with zero code changes.

The provider auto-disables (0 accounts) if the binary is not on PATH, so it is
safe to leave enabled next to the GUI route.
"""

from __future__ import annotations

from ...cli import CliProvider


class WorkbuddyCliProvider(CliProvider):
    name = "workbuddy_cli"
    binary = "workbuddy"  # override in config if your CLI is named differently
    prompt_flag = "-p"
    model_flag = "--model"
    timeout = 180.0
    served_models: tuple[str, ...] = ()
