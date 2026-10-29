"""Let Antigravity Tools keep our credential alive — instead of re-implementing its OAuth.

Measured 2026-10-29 on this machine:

* Our own Google consent for Antigravity does **not** work: the client pair we could read
  out of the installed app is refused by Google (`invalid_client`), even though the app
  itself signs in fine. The app has its own runtime client, and it also owns the account
  (`antigravity-tools list` → one account, 正常).
* `antigravity-tools switch 1 --target agy` pushes that account into the **agy CLI's
  Keychain slot** — the very slot our leg already reads
  (`desktop_stores.antigravity_desktop_token_status()`). Right after the switch the leg
  saw a fresh access token (≈55 minutes of life) and its eight models became
  selectable, with **no browser and no consent page**.

So the leg's credential source is their tool, and this module is the seam: when the token
in the shared slot has gone stale, ask the tool to refresh it and read it again. We do not
read, copy or store their credentials — we ask the tool that already owns them.

Nothing here is Antigravity-specific plumbing: the tool is a local binary, the slot is the
one the agy CLI uses, and the leg is unchanged in every other respect.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("quota_hub")

#: Where the tool lives. The first existing path wins; the PATH lookup is the fallback.
TOOL_PATHS: tuple[str, ...] = (
    r"D:\Software\Antigravity Tools\antigravity-tools.exe",
    r"C:\Program Files\Antigravity Tools\antigravity-tools.exe",
)

#: The tool refuses to touch another product's slot; `agy` is the one we read.
TARGET = "agy"


def find_tool() -> str:
    """The tool's binary, or "" when it is not installed here."""
    for candidate in TOOL_PATHS:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("antigravity-tools") or ""


def refresh_via_tool(*, timeout: float = 60.0) -> tuple[bool, str]:
    """Ask the tool to re-push its account into the shared slot.

    Returns (ok, detail). `ok` means the tool ran and reported success — it is the
    tool's own verdict, not ours, because it owns the credential.
    """
    binary = find_tool()
    if not binary:
        return False, "这台机器上没有 Antigravity Tools"
    try:
        proc = subprocess.run(
            [binary, "switch", "--target", TARGET],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(os.environ),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, out.strip()[:200] or f"退出码 {proc.returncode}"
    log.info("antigravity: credential refreshed through Antigravity Tools")
    return True, out.strip()[:200]
