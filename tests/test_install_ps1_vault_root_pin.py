"""The installer must pin BOTH memory roots (WP-INSTALL-MINDROOT, second half).

Why this exists: `install.ps1` already pinned MIND_ROOT, and my 2026-10-14 split added a
second root (`VAULT_ROOT`) that only I had pinned, by hand, in the registry. A machine
installed by the installer therefore gets half the memory surface: `plobi/mind/paths.py`
resolves the brain but refuses a `Vault/…` write once the old in-brain surface is gone.
That is not a missing convenience variable — it is a functional half-machine.

Two tests, per the "每格≤两条" rule: one pins the mechanism (same home, same API and
scope, same idempotency guard, no drive letters, no fabricated directory), one runs the
real PowerShell function and checks what it actually writes.

The control-character assertion is not decoration: writing this change through a shell
heredoc put a literal backspace (0x08) into `scripts\backup_repos.py` — the string read
fine on screen, `grep` showed no problem, and the docs gate stayed GREEN. Only a byte
count caught it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_PS1 = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def _function_body(name: str) -> str:
    source = INSTALL_PS1.read_text(encoding="utf-8")
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"unterminated function {name}")


def test_vault_root_is_pinned_by_the_same_mechanism_as_the_brain():
    body = _function_body("Set-MindRootEnv")

    assert re.search(r'Join-Path\s+\$PlobiHomePath\s+"vault"', body), (
        "VAULT_ROOT must be derived as <the chosen home>/vault, same single home source"
    )
    assert '[Environment]::SetEnvironmentVariable("VAULT_ROOT", $vaultRoot, $Scope)' in body
    assert '[Environment]::GetEnvironmentVariable("VAULT_ROOT", $Scope)' in body
    assert re.search(r"if \(-not \$existingVault -or \$existingVault -ne \$vaultRoot\)", body), (
        "same do-not-rewrite-if-equal guard as the MIND_ROOT pin, so re-running is idempotent"
    )
    assert "$env:VAULT_ROOT" in body, "the just-set value must be visible to this process too"

    # one home, one place: no second default, no drive letters (ruling 48.1 / 79)
    assert "LOCALAPPDATA" not in body
    assert not re.search(r"[A-Za-z]:[\\/]", body)
    # and never fabricate the surface: an empty dir reads as "configured" one layer away
    assert not re.search(r"New-Item|\bmkdir\b|CreateDirectory", body)

    # it has no remote by design, so the recovery hint must not claim otherwise
    assert "no remote by design" in body
    assert "bundles/keep" in body and "scripts/backup_repos.py" in body
    # the byte-level regression: a \b eaten by an escape leaves a backspace in the file
    assert "\x08" not in body and "\x00" not in body


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_running_the_installer_step_pins_both_roots(tmp_path: Path):
    """Execute the real function with Scope=Process (never touches live HKCU)."""
    home = tmp_path / "plobi-home"
    home.mkdir()
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        'function Write-Info { param([string]$Message) Write-Output "INFO:$Message" }\n'
        'function Write-Success { param([string]$Message) Write-Output "OK:$Message" }\n'
        + _function_body("Set-MindRootEnv").replace("function Set-MindRootEnv",
                                                    "function Set-MindRootEnv", 1)
        + f"\nSet-MindRootEnv -PlobiHomePath '{home}' -Scope Process\n"
        + 'Write-Output "MIND:$env:MIND_ROOT"\n'
        + 'Write-Output "VAULT:$env:VAULT_ROOT"\n',
        encoding="ascii",
    )
    done = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                           "-File", str(script)], capture_output=True, text=True, timeout=180)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out

    mind = str(home / "mind")
    vault = str(home / "vault")
    assert f"MIND:{mind}" in out, out
    assert f"VAULT:{vault}" in out, out
    assert f"OK:Set VAULT_ROOT={vault}" in out, out
    # two roots are siblings under one home -- the whole point of deriving, not listing
    assert Path(out.split("VAULT:")[1].splitlines()[0].strip()).parent == home
    assert "not at" in out and "no remote by design" in out, (
        "a fresh machine is told plainly: neither surface has been restored yet"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
