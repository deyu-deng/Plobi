"""Regression: the Windows installer must register the brain's location too.

裁定 72 moved the official brain out of the code repo to ``<home>/mind`` and made
``MIND_ROOT`` the only accepted pointer.  ``plobi/mind/paths.py::resolve_root()``
honours it and — deliberately — does **not** fall back once it is set-but-missing.
Before this fix ``install.ps1`` pinned ``PLOBI_HOME`` only (``grep -c MIND_ROOT
scripts/install.ps1`` was 0), so a machine installed by the script woke up with the
brain resolving to the long-removed in-repo sibling directory, and pinning it again
by hand was a step nobody documented as part of installing.

The contract is locked two ways: at the source level (the script only runs on
Windows), **and** by running the derivation for real in PowerShell — against a temp
home and the in-process env scope only, never the live user registry.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_INSTALL_PS1 = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@pytest.fixture(scope="module")
def source() -> str:
    return _INSTALL_PS1.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """Return the text of a PowerShell ``function <name> { ... }`` block."""
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : i + 1]
    raise AssertionError(f"unterminated function body for {name}")


# ---------------------------------------------------------------- source contracts


def test_derivation_function_exists_and_uses_the_home_it_is_given(source: str):
    """The brain's path is derived from the home it is handed — never a second default."""
    body = _function_body(source, "Set-MindRootEnv")
    assert re.search(r'Join-Path\s+\$PlobiHomePath\s+"mind"', body), (
        "MIND_ROOT must be <the chosen home>\\mind, spelled as a derivation"
    )
    # A second hardcoded home is the exact bug this knife closes.
    assert "LOCALAPPDATA" not in body, (
        "the brain's location must not re-derive a default home; one home, one place "
        "(install.ps1's $PlobiHome parameter is that place)"
    )
    assert not re.search(r"[A-Za-z]:[\\/]", body), (
        "no literal drive letter may appear in the brain's location logic (ruling 48.1 / 79)"
    )


def test_pinned_by_the_same_mechanism_and_scope_as_plobi_home(source: str):
    """Same API, same scope, same "don't rewrite if equal" guard as the PLOBI_HOME pin."""
    body = _function_body(source, "Set-MindRootEnv")
    assert '[Environment]::SetEnvironmentVariable("MIND_ROOT", $mindRoot, $Scope)' in body
    assert '[Environment]::GetEnvironmentVariable("MIND_ROOT", $Scope)' in body
    assert re.search(r'\[string\]\$Scope\s*=\s*"User"', body), (
        "default scope must be User — a process-only value is exactly what does not "
        "survive into an autostart or scheduled-task process"
    )
    assert re.search(r"if \(-not \$existing -or \$existing -ne \$mindRoot\)", body), (
        "must be idempotent, mirroring the PLOBI_HOME guard it sits next to"
    )


def test_path_stage_calls_it_from_the_final_home(source: str):
    """Stage-Path must register the brain from the home that actually won."""
    body = _function_body(source, "Set-PathVariable")
    call = body.find("Set-MindRootEnv")
    assert call != -1, "Set-PathVariable must call Set-MindRootEnv"
    pin = body.find('[Environment]::SetEnvironmentVariable("PLOBI_HOME"')
    assert pin != -1, "expected the PLOBI_HOME pin in Set-PathVariable"
    first_line = body[call : body.index("\n", call)]
    assert "$PlobiHome" in first_line, (
        f"the call must pass $PlobiHome, not compute a home of its own: {first_line!r}"
    )


def test_missing_directory_is_reported_not_created(source: str):
    """An empty <home>/mind resolves to a non-repo path — worse than "not configured"."""
    body = _function_body(source, "Set-MindRootEnv")
    assert "Test-Path" in body, "must say so when the brain is not there yet"
    assert not re.search(r"New-Item|\bmkdir\b|CreateDirectory", body), (
        "the installer must not fabricate the vault directory"
    )


# ------------------------------------------------------------------- live execution


def _extract_function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def _run_derivation(tmp_path: Path, preexisting: str | None = None) -> tuple[str, str]:
    """Execute the real Set-MindRootEnv in PowerShell with Scope=Process.

    Scope=Process writes only the *current* process environment — the live HKCU
    user registry is never touched, so this is safe to run on a machine that
    already has MIND_ROOT pinned by hand.  Returns (stdout, expected brain path).
    """
    fn = _extract_function(_INSTALL_PS1.read_text(encoding="utf-8"), "Set-MindRootEnv")
    home = tmp_path / "plobi-home"
    home.mkdir(exist_ok=True)
    expected = str(home / "mind")
    q = chr(39)  # single quote, kept out of the f-string braces below
    seed = f"$env:MIND_ROOT = {q}{preexisting}{q}; Write-Output 'SEEDED'\n" if preexisting else ""
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        'function Write-Info { param([string]$Message) Write-Output "INFO:$Message" }\n'
        'function Write-Success { param([string]$Message) Write-Output "OK:$Message" }\n'
        + fn
        + "\n"
        + seed
        + f"Set-MindRootEnv -PlobiHomePath {q}{home}{q} -Scope Process\n"
        + 'Write-Output "RESULT:$env:MIND_ROOT"\n'
    )
    ps1 = tmp_path / "probe.ps1"
    ps1.write_text(script, encoding="ascii")
    completed = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    # stderr is folded in so a child that died quietly still shows up in the assertion.
    return completed.stdout + completed.stderr, expected


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_powershell_registers_the_derived_value(tmp_path: Path):
    """Run it for real: afterwards MIND_ROOT is <home>\\mind, not an echo of the docs."""
    out, expected = _run_derivation(tmp_path)
    assert f"RESULT:{expected}" in out, out
    assert "OK:Set MIND_ROOT=" in out, out
    assert "not at" in out, "a fresh machine must be told the vault is not restored yet"


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_powershell_is_idempotent_when_already_registered(tmp_path: Path):
    """Second run with the right value rewrites nothing and still resolves the same."""
    _, expected = _run_derivation(tmp_path)
    out, _ = _run_derivation(tmp_path, preexisting=expected)
    assert "SEEDED" in out, out
    assert f"RESULT:{expected}" in out, out
    assert "INFO:MIND_ROOT already configured" in out, out
    assert "OK:Set MIND_ROOT=" not in out, out


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_pinned_value_is_what_the_real_resolver_returns(tmp_path: Path):
    """Hand the value PowerShell pinned to the real resolver, in a fresh process.

    Two halves, both measured rather than quoted from the docs:
      * brain directory absent  → resolve_root() is None.  It must NOT silently fall
        back into the repo — that fallback is what 裁定 72 removed.
      * brain directory present → resolve_root() is exactly what the installer pinned.
    """
    import sys

    out, expected = _run_derivation(tmp_path)
    pinned = [ln for ln in out.splitlines() if ln.startswith("RESULT:")]
    assert pinned and pinned[0] == f"RESULT:{expected}", out

    probe = tmp_path / "resolver_probe.py"
    probe.write_text(
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from plobi.mind.paths import resolve_root\n"
        'print("RESOLVED:" + str(resolve_root()))\n',
        encoding="ascii",
    )
    repo = str(_INSTALL_PS1.parents[1])

    def resolve_with(value: str) -> str:
        env = dict(os.environ)
        env["MIND_ROOT"] = value
        done = subprocess.run(
            [sys.executable, str(probe), repo],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        lines = [ln for ln in done.stdout.splitlines() if ln.startswith("RESOLVED:")]
        assert lines, f"resolver printed nothing: {done.stdout!r} {done.stderr[-400:]!r}"
        return lines[0][len("RESOLVED:"):]

    assert resolve_with(pinned[0][len("RESULT:"):]) == "None", (
        "set-but-missing must resolve to None instead of falling back into the repo"
    )
    (tmp_path / "plobi-home" / "mind").mkdir(exist_ok=True)
    got = resolve_with(pinned[0][len("RESULT:"):])
    assert os.path.normcase(got) == os.path.normcase(expected), got


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_install_ps1_still_parses():
    """A 3.5k-line script edited by agents must still be valid PowerShell.

    A syntax error in a branch that only runs on a fresh install stays invisible
    until somebody's machine cannot install — and this script is that gate.
    """
    ps = (
        "$errs = $null; $tokens = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{_INSTALL_PS1.as_posix()}', [ref]$tokens, [ref]$errs); "
        'Write-Output "ERRORS:$($errs.Count)"'
    )
    completed = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "ERRORS:0" in completed.stdout, completed.stdout + completed.stderr


def test_no_literal_drive_letter_in_the_new_lines(source: str):
    """Ruling 48.1 / 79: this machine's trap is a drive-letter literal read as relative."""
    body = _function_body(source, "Set-MindRootEnv") + _function_body(source, "Set-PathVariable")
    for line in body.splitlines():
        if "MIND_ROOT" in line or "MindRoot" in line:
            assert not re.search(r"[A-Za-z]:[\\/]{1,2}[A-Za-z]", line), line
