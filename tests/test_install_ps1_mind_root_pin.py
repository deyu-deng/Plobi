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
    """Same API, same scope — and it does NOT move a pointer that is already set.

    2026-11-07：这一段以前是「与我的不一样就改写成我的」⇒ 桌面端每次启动跑 bootstrap
    都把用户自己指的那一格拖回安装器的默认（这台：%LOCALAPPDATA%\\plobi，C 盘）。
    脑住在哪是**人的决定**（它是自己那个仓，有独立远端），口径与 Python 侧
    ``paths.py::_surface_pointer()`` 同一条：宁可不静默换家。
    """
    body = _function_body(source, "Set-MindRootEnv")
    assert '[Environment]::SetEnvironmentVariable("MIND_ROOT", $mindRoot, $Scope)' in body
    assert '[Environment]::GetEnvironmentVariable("MIND_ROOT", $Scope)' in body
    assert re.search(r'\[string\]\$Scope\s*=\s*"User"', body), (
        "default scope must be User — a process-only value is exactly what does not "
        "survive into an autostart or scheduled-task process"
    )
    assert re.search(r"if \(-not \$existing\)\s*\{", body), (
        "已经钉着的指针不许被改写：脑住在哪要人来定（口径同 paths.py::_surface_pointer()）"
    )
    assert "-or $existing -ne $mindRoot" not in body, (
        "旧写法是「与我的不一样就改写成我的」⇒ 每次启动都替人搬家"
    )


def test_the_surface_pointer_is_the_home_itself(source: str):
    """2026-11-03 起人读面住在家目录本体，不再有 <家>\\surface 这一层。

    旧形状把指针指到一处根本不存在的地方 ⇒ 人读面读不到（这台 11-07 现核：
    注册表里那一格指的就是那个不存在的旧形状）。
    """
    body = _function_body(source, "Set-MindRootEnv")
    assert '$surfaceRoot = $PlobiHomePath' in body, (
        "人读面的落点就是家本体（裁定 2026-11-03）"
    )
    assert 'Join-Path $PlobiHomePath "surface"' not in body, (
        "旧形状 <家>\\surface 指到一处不存在的地方 ⇒ 人读面读不到"
    )
    assert re.search(r"if \(-not \$existingSurface\)\s*\{", body), (
        "人读面的指针同样不许覆盖已经钉着的那一格"
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
    # 探针里**显式**把那格置空（preexisting=None 的意思就是"没钉"）：这台机器的测试环境
    # 本来就带着一个 MIND_ROOT（conftest 的假脑），不显式清掉就分不出"没钉"与"已钉"。
    if preexisting:
        seed = f"$env:MIND_ROOT = {q}{preexisting}{q}; Write-Output 'SEEDED'\n"
    else:
        seed = "$env:MIND_ROOT = ''; Write-Output 'SEEDED'\n"
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
    # UTF-8 **with BOM**：Windows PowerShell 5.1 reads a BOM'd file as UTF-8, a BOM-less
    # one as ANSI ⇒ the function body (which carries the script's own Chinese comments)
    # would come back mangled.  The harness used to write ASCII and only passed because
    # that one function happened to be English-only.
    ps1.write_text(script, encoding="utf-8-sig")
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
def test_powershell_does_not_move_a_pointer_that_is_already_set(tmp_path: Path):
    """已经钉在别处的指针**不许被改写**（脑住在哪是人的决定）。

    2026-11-07 那台的真事：脑在 ``Plobi-Dev\\mind``，而安装器每次启动都把 MIND_ROOT
    拖回 ``<家>\\mind``（那个目录根本不存在）⇒ 后端按 set-but-missing 读不到脑。
    """
    elsewhere = tmp_path / "elsewhere-brain"
    elsewhere.mkdir()
    out, _ = _run_derivation(tmp_path, preexisting=str(elsewhere))
    assert f"RESULT:{elsewhere}" in out, out
    assert "OK:Set MIND_ROOT=" not in out, out
    assert "already configured" in out, out


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
