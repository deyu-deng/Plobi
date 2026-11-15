"""Marvis provider via a locally-installed Marvis CLI (spawn-based).

Compliant route (opendesign-style): if the Marvis build you use ships a CLI
(e.g. openmarvis, marvisx-cli, or any future Tencent Marvis CLI), this provider
spawns it as a subprocess and translates its stdout into an OpenAI ChatCompletion
— the same mechanism as AntigravityCliProvider. Every CLI-specific detail
(binary, prompt flag, model flag, served models) comes from config, so this one
class covers any CLI-based Marvis with zero code changes.

IMPORTANT — Tencent consumer Marvis (marvis.qq.com):
  That product is a *native* system-level GUI app (WinML / OpenVINO / NPU, not
  Electron). It has NO CLI and NO documented inbound API, so it CANNOT use this
  provider. Driving it requires GUI automation (see MarvisGuiProvider), which is
  the GUI analog of spawn-CLI: we type into its chat box and read the reply via
  the OS accessibility tree — no token scraping, but fragile and platform-bound.
"""

from __future__ import annotations

from ...cli import CliProvider


class MarvisCliProvider(CliProvider):
    name = "marvis_cli"
    # binary / flags / models are entirely config-driven for this generic adapter.
    served_models: tuple[str, ...] = ()
