"""Tencent Marvis (marvis.qq.com) provider via GUI automation.

Tencent's consumer Marvis is a *native* system-level GUI app (WinML / OpenVINO /
NPU, NOT Electron) with no CLI and no inbound API. It therefore cannot use the
spawn-CLI route (marvis_cli.py). The only compliant-ish way to use its quota
from quota_hub is the GUI analog of spawn-CLI: drive its chat box like a human
(see quota_hub/providers/gui.py for the full engine + CAVEATS).

All control selectors are config-driven; run `quota_hub marvis-dump` on the target
Windows machine to discover them. See quota_hub/docs/GUI_AUTOMATION.md.
"""

from __future__ import annotations

from ...gui import GuiProvider


class MarvisGuiProvider(GuiProvider):
    name = "marvis_gui"
    display_name = "Tencent Marvis"
    served_models: tuple[str, ...] = ()
