"""The installer must pin BOTH memory roots, and the human root has two names
(WP-NOTES-DIARY-DOSSIER, 2026-11-10).

Why this exists: `install.ps1` pins MIND_ROOT, and the 2026-10-14 split added a
second root for the human side that only one hand had pinned, in the registry. A
machine installed by the installer therefore gets half the memory surface:
`plobi/mind/paths.py` resolves the brain but refuses a `Notes/…` write once the
old in-brain copy is gone. That is not a missing convenience variable — it is a
functional half-machine.

Three facts have moved since the first version of this file, and each one is a
test case now:

* **2026-11-03** — the human side lives in the **home itself**
  (`<home>\notes`, `<home>\diary`, `<home>\dossiers`), not in `<home>\surface`.
  The old shape pointed the pointer at a directory that does not exist.
* **2026-11-08** — the installer writes **process env only**; it no longer
  writes `PLOBI_HOME` / `MIND_ROOT` / the human root into `HKCU\Environment`
  (those gates are shared by the packaged and the dev identity; whichever
  installs last drags the other one home).
* **2026-11-10** — that pointer was **renamed**: `HUMAN_ROOT` is the new name,
  `SURFACE_ROOT` is still accepted. Both are written, and they must carry the
  **same value** — one home, two spellings.

Two static tests pin the mechanism (derivation, both names, same value, no
drive letters, no fabricated directory, no HKCU write) and one real PowerShell
run pins what it actually writes.

The control-character assertion is not decoration: writing this change through a
shell heredoc put a literal backspace (0x08) into `scripts\backup_repos.py` — the
string read fine on screen, `grep` showed no problem, and the docs gate stayed
GREEN. Only a byte count caught it.
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


def _code_only(body: str) -> str:
    """The body with the comments stripped.

    The "no drive letters" rule is about **code**: a comment may of course name
    a real path as an example (the 11-08 note names ``D:\\Data\\AppData\\Plobi\\mind``).
    Checking the comments too would flag documentation as a violation.
    """
    out = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(out)


def test_human_root_is_the_home_itself_and_carries_both_names():
    body = _function_body("Set-MindRootEnv")

    # 2026-11-03: the human side lives in the home itself — there is no
    # `<home>\surface` layer any more.
    assert re.search(r"\$surfaceRoot\s*=\s*\$PlobiHomePath\b", body), (
        "the human root must be the home itself, not <home>/surface"
    )
    assert not re.search(r'Join-Path\s+\$PlobiHomePath\s+"surface"', body), (
        "the old <home>/surface shape is the one that pointed at nothing"
    )

    # 2026-11-10: one pointer, two spellings. The new name is written first and
    # the old one mirrors it — a backend that only knows the old name must not
    # lose the human side, and two different values would be worse than none.
    assert "$env:HUMAN_ROOT = $inheritedHuman" in body
    assert "$env:SURFACE_ROOT = $inheritedHuman" in body, (
        "the legacy name must mirror the new one — same value, never a second home"
    )
    # new name first, old name only as the fallback
    assert body.index("$env:HUMAN_ROOT") < body.index("$env:SURFACE_ROOT"), (
        "HUMAN_ROOT is the new name and takes precedence"
    )
    assert re.search(r"\$inheritedHuman\s*=\s*\$env:HUMAN_ROOT", body)
    assert re.search(r"IsNullOrWhiteSpace\(\$inheritedHuman\)\)?\s*\{\s*\$inheritedHuman\s*=\s*\$env:SURFACE_ROOT", body), (
        "SURFACE_ROOT is the fallback when the new name is absent"
    )

    # 2026-11-08: process env only. Writing the user-level gate is what made
    # the packaged and the dev identity drag each other home.
    assert "SetEnvironmentVariable" not in body, (
        "the installer must not write HKCU\\Environment for these pointers"
    )
    assert "$env:MIND_ROOT" in body, "the brain pointer must be visible to this process"

    # one home, one place: no second default, no drive letters (ruling 48.1 / 79)
    assert "LOCALAPPDATA" not in body
    assert not re.search(r"[A-Za-z]:[\\/]", _code_only(body)), (
        "no literal drive letters in code — the home comes in as a parameter"
    )
    # and never fabricate the human side: an empty dir reads as "configured" one layer away
    assert not re.search(r"New-Item|\bmkdir\b|CreateDirectory", body)

    # it has no remote by design, so the recovery hint must not claim otherwise
    assert "no remote by design" in body
    assert "bundles/keep" in body and "scripts/backup_repos.py" in body
    # the byte-level regression: a \b eaten by an escape leaves a backspace in the file
    assert "\x08" not in body and "\x00" not in body


def _run_installer_step(tmp_path: Path, *, inherited: dict[str, str] | None = None):
    """Execute the real function (never touches live HKCU).

    ``MIND_ROOT`` is cleared unless the scenario inherits one: the machine this
    runs on may have a pointer of its own, and the installer never moves an
    inherited pointer — so an inherited one would make the derived value
    untestable.
    """
    home = tmp_path / "plobi-home"
    home.mkdir(exist_ok=True)
    script = tmp_path / "probe.ps1"
    preamble = ""
    for name, value in (inherited or {}).items():
        preamble += f'$env:{name} = "{value}"\n'
    if "MIND_ROOT" not in (inherited or {}):
        preamble += "$env:MIND_ROOT = ''\n"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + preamble
        + "function Write-Info { param([string]$Message) Write-Output \"INFO:$Message\" }\n"
        + "function Write-Success { param([string]$Message) Write-Output \"OK:$Message\" }\n"
        + _function_body("Set-MindRootEnv")
        + f"\nSet-MindRootEnv -PlobiHomePath '{home}'\n"
        + 'Write-Output "MIND:$env:MIND_ROOT"\n'
        + 'Write-Output "HUMAN:$env:HUMAN_ROOT"\n'
        + 'Write-Output "SURFACE:$env:SURFACE_ROOT"\n',
        # UTF-8 **with BOM**: PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI
        # and mangles the Chinese comments in the extracted body.
        encoding="utf-8-sig",
    )
    done = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    return home, out


def _value(out: str, label: str) -> str:
    match = re.search(rf"^{label}:(.*)$", out, re.MULTILINE)
    assert match, out
    return match.group(1).strip()


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_running_the_installer_step_pins_both_roots_with_one_human_home(tmp_path: Path):
    """A fresh process: both pointers come from the one home that won."""
    home, out = _run_installer_step(tmp_path)

    assert _value(out, "MIND") == str(home / "mind"), out
    # the human side is the home itself — one home, no <home>/surface layer
    assert _value(out, "HUMAN") == str(home), out
    # both spellings, same value — a backend that only knows the old name still finds it
    assert _value(out, "SURFACE") == _value(out, "HUMAN"), out
    assert "not at" in out and "no remote by design" in out, (
        "a fresh machine is told plainly: neither half has been restored yet"
    )


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_an_inherited_pointer_is_never_moved_and_the_two_names_agree(tmp_path: Path):
    """An already-pinned human root stays where the human put it.

    The installer must not silently repoint it (that is how the packaged and the
    dev identity dragged each other home). The old name is still honoured: a
    machine that only has `SURFACE_ROOT` keeps that home, and the new name
    mirrors it rather than becoming a second, different one.
    """
    pinned = str(tmp_path / "elsewhere")
    home, out = _run_installer_step(tmp_path, inherited={"SURFACE_ROOT": pinned})

    assert _value(out, "HUMAN") == pinned, out
    assert _value(out, "SURFACE") == pinned, out
    assert "not moving it" in out, out


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_the_new_name_wins_when_both_are_inherited(tmp_path: Path):
    """Both spellings inherited and different: the new name decides, and the
    old name is rewritten to match it (never two homes)."""
    new = str(tmp_path / "new-home")
    old = str(tmp_path / "old-home")
    home, out = _run_installer_step(
        tmp_path, inherited={"HUMAN_ROOT": new, "SURFACE_ROOT": old}
    )

    assert _value(out, "HUMAN") == new, out
    assert _value(out, "SURFACE") == new, out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
