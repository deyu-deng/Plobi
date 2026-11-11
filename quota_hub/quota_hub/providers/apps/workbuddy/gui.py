"""Workbuddy (desktop GUI) provider via Windows UI Automation.

Some Workbuddy builds are distributed as a desktop GUI with no CLI and no
inbound API. This provider drives the chat box the way a human would — the GUI
analog of spawn-CLI (see quota_hub/providers/gui.py for the engine + CAVEATS). All
control selectors are config-driven; run `quota_hub workbuddy-dump` on the target
Windows machine to discover them. See quota_hub/docs/GUI_AUTOMATION.md.
"""

from __future__ import annotations

from ...gui import GuiProvider


class WorkbuddyGuiProvider(GuiProvider):
    name = "workbuddy_gui"
    display_name = "WorkBuddy"
    served_models: tuple[str, ...] = ()
