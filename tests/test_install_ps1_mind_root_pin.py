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

2026-11-16（现况）：**钉的方式变了**（WP-DATA-ISOLATION，2026-11-08）。
``MIND_ROOT`` / ``PLOBI_HOME`` / 人读面那一格不再是"往用户级注册表里写一格"，
而是**只设本次安装进程的 ``$env``**：那几格是两份身份（装机版 / 开发态）共用的门牌，
钉死一份就把另一份的家也带偏了。现在签的是：
  · 由**同一个家**派生（``Join-Path $PlobiHomePath "mind"``），不许出现第二处默认；
  · 只写本次进程的 ``$env``，**不写注册表**；
  · **已经钉着的指针不许被改写**（脑住在哪是人的决定，口径同
    ``paths.py::_surface_pointer()``）。
用户级的残留由 ``Remove-LegacySharedEnvVars``（装机器时顺手收）与卸载那一档负责。
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


def _code_lines(body: str) -> list[str]:
    """The function's executable lines — PowerShell line comments dropped.

    裁定 48.1 / 79 禁的是**逻辑里**的盘符字面量。函数头里那段解释历史的话
    （"开发态 MIND_ROOT 指向 D:\\Data\\AppData\\Plobi\\mind"）是**注释**，
    不是会被人执行的路径 —— 判据要盯的是前者。
    """
    return [
        line
        for line in body.splitlines()
        if not line.strip().startswith("#")
    ]


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
    code = "\n".join(_code_lines(body))
    assert not re.search(r"[A-Za-z]:[\\/]", code), (
        "no literal drive letter may appear in the brain's location logic (ruling 48.1 / 79)"
    )


def test_pinned_by_the_same_mechanism_and_scope_as_plobi_home(source: str):
    """Same mechanism, same scope as PLOBI_HOME — and it does NOT move a pointer
    that is already set.

    2026-11-07：这一段以前是「与我的不一样就改写成我的」⇒ 桌面端每次启动跑 bootstrap
    都把用户自己指的那一格拖回安装器的默认（这台：%LOCALAPPDATA%\\plobi，C 盘）。
    脑住在哪是**人的决定**（它是自己那个仓，有独立远端），口径与 Python 侧
    ``paths.py::_surface_pointer()`` 同一条：宁可不静默换家。

    2026-11-16（现况）：机制与作用域又变了一次——**两边都不再写用户级注册表**，
    只设本次安装进程的 ``$env``（WP-DATA-ISOLATION：那一格是两份身份共用的门牌）。
    用户级的残留由 ``Remove-LegacySharedEnvVars`` 收回、由卸载那一档清干净。
    """
    body = _function_body(source, "Set-MindRootEnv")

    # 1. 不许再写用户级注册表（一个字节都不许）。
    assert "SetEnvironmentVariable" not in body, (
        "脑的指针不再写用户级注册表（WP-DATA-ISOLATION）：只设本次安装进程的 $env"
    )
    assert "GetEnvironmentVariable" not in body, (
        "脑的指针不再从用户级注册表读：只认本次安装进程继承的那一串"
    )

    # 2. 只设本次进程的 $env，且与 PLOBI_HOME 同一个机制。
    assert "$env:MIND_ROOT = $mindRoot" in body
    assert "$env:MIND_ROOT" in body

    # 3. 已经钉着的指针不许被改写：赋值只许出现在 else 那一支（"没钉"的那一支）。
    assert "if ($env:MIND_ROOT)" in body, (
        "已经钉着的指针要认出来（不许无声改写）"
    )
    else_at = body.index("} else {")
    assign_at = body.index("$env:MIND_ROOT = $mindRoot")
    assert else_at < assign_at, (
        "已经钉着的指针不许被改写：赋值只许在 else（没钉）那一支"
    )
    assert "-or $existing -ne $mindRoot" not in body, (
        "旧写法是「与我的不一样就改写成我的」⇒ 每次启动都替人搬家"
    )


def test_the_surface_pointer_is_the_home_itself(source: str):
    """2026-11-03 起人读面住在家目录本体，不再有 <家>\\surface 这一层。

    旧形状把指针指到一处根本不存在的地方 ⇒ 人读面读不到（这台 11-07 现核：
    注册表里那一格指的就是那个不存在的旧形状）。

    2026-11-13 起那一格只叫 ``HUMAN_ROOT``，旧名 ``SURFACE_ROOT`` 彻底停用
    （读它等于把旧指针当成配置继续用）——所以**代码里不许再出现那个旧名**。
    """
    body = _function_body(source, "Set-MindRootEnv")
    assert '$surfaceRoot = $PlobiHomePath' in body, (
        "人读面的落点就是家本体（裁定 2026-11-03）"
    )
    assert 'Join-Path $PlobiHomePath "surface"' not in body, (
        "旧形状 <家>\\surface 指到一处不存在的地方 ⇒ 人读面读不到"
    )

    code = "\n".join(_code_lines(body))
    assert "SURFACE_ROOT" not in code, (
        "旧名 SURFACE_ROOT 已彻底停用（2026-11-13）：代码里不许再读它"
    )

    # 人读面那一格同样**不许覆盖已经钉着的那一格**（口径同上）。
    assert "$inheritedHuman = $env:HUMAN_ROOT" in body
    assert "$inheritedHuman = $surfaceRoot" in body, (
        "没钉着才落回家本体"
    )
    assert "$env:HUMAN_ROOT = $inheritedHuman" in body, (
        "人读面的指针只许照着继承来的那一串写回去，不许改写它"
    )


def test_path_stage_calls_it_from_the_final_home(source: str):
    """Stage-Path must register the brain from the home that actually won."""
    body = _function_body(source, "Set-PathVariable")
    call = body.find("Set-MindRootEnv")
    assert call != -1, "Set-PathVariable must call Set-MindRootEnv"
    # 2026-11-08 起 PLOBI_HOME 也不再写用户级注册表，只设本次进程的 $env。
    pin = body.find("$env:PLOBI_HOME = $PlobiHome")
    assert pin != -1, "expected the PLOBI_HOME pin in Set-PathVariable"
    assert '[Environment]::SetEnvironmentVariable("PLOBI_HOME"' not in body, (
        "PLOBI_HOME 不再写用户级注册表（WP-DATA-ISOLATION）"
    )
    assert pin < call, "PLOBI_HOME 先落定，再从它派生脑的落点"
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
    """Execute the real Set-MindRootEnv in PowerShell.

    2026-11-16（现况）：这个函数**不再有 ``-Scope`` 参数**了——它根本不写注册表，
    只设**本次安装进程**的 ``$env:MIND_ROOT`` / ``$env:HUMAN_ROOT``
    （WP-DATA-ISOLATION）。所以这台机器上那个被人手钉过的 HKCU\\Environment 里的
    MIND_ROOT **一个字节都不会被碰到**，探针可以放心跑。
    Returns (stdout, expected brain path).
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
        + f"Set-MindRootEnv -PlobiHomePath {q}{home}{q}\n"
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
    assert "MIND_ROOT (this process only, user env untouched)" in out, out
    assert "not at" in out, "a fresh machine must be told the vault is not restored yet"


@pytest.mark.skipif(_POWERSHELL is None, reason="no PowerShell on this machine")
def test_powershell_is_idempotent_when_already_registered(tmp_path: Path):
    """Second run with the right value rewrites nothing and still resolves the same."""
    _, expected = _run_derivation(tmp_path)
    out, _ = _run_derivation(tmp_path, preexisting=expected)
    assert "SEEDED" in out, out
    assert f"RESULT:{expected}" in out, out
    assert "MIND_ROOT (this process only, already inherited)" in out, out
    assert "MIND_ROOT (this process only, user env untouched)" not in out, out


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
    assert "MIND_ROOT (this process only, user env untouched)" not in out, out
    assert "already inherited" in out, out
    assert "not moving it" in out, (
        "钉在别处的指针要**说清楚没被搬**（口径：脑住在哪是人的决定）"
    )


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
    for line in _code_lines(body):
        if "MIND_ROOT" in line or "MindRoot" in line:
            assert not re.search(r"[A-Za-z]:[\\/]{1,2}[A-Za-z]", line), line
