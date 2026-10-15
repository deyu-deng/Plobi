"""WP-INSTALL-DATADIR：装到别人机器上时，那份运行数据落在哪里，谁问、谁挡、谁记。

裁定 142 / 待决 #24 结案后的形状：`install.ps1` 之前**静默**把落点定成 `$env:PLOBI_HOME` 否则
`%LOCALAPPDATA%\plobi`（`scripts/install.ps1:26`）。这台机器有明文盘规
（`_POLICY\directory-policy.md`「软件运行数据禁止残留 C 盘」），而静默默认恰好落在被禁的那个位置。

契约锁两层。源码层：一条真源（选定值只写回 `$PlobiHome`）、不新建第二处 HKCU 写入、逻辑里不许出现
字面盘符（裁定 48.1 / 79），问与校验排在任何建目录之前。行为层**真跑 PowerShell**（口径 27：脚本类
不许只测 mock）：合规放行、四条不合规各挡一次且理由互不相同、非交互不问但仍打印仍钉、显式给了
`-InstallDir` 就不覆盖。落点一律指向临时目录，**这台机器的 HKCU 一字不碰**（`Set-MindRootEnv` 那族
邻居同样只用 Process 作用域，见 `test_install_ps1_mind_root_pin.py`）。
"""

import os
import re
import shutil
import string
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

_INSTALL_PS1 = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

_SOURCE = _INSTALL_PS1.read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(_POWERSHELL is None, reason="这台没有 PowerShell，install.ps1 的行为无从真跑（不是通过）")


# --------------------------------------------------------------------- source reading


def _function_text(name: str) -> str:
    start = _SOURCE.index(f"function {name}")
    brace = _SOURCE.index("{", start)
    depth = 0
    for i in range(brace, len(_SOURCE)):
        if _SOURCE[i] == "{":
            depth += 1
        elif _SOURCE[i] == "}":
            depth -= 1
            if depth == 0:
                return _SOURCE[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def _function_body(name: str) -> str:
    text = _function_text(name)
    return text[text.index("{") :]


# ---------------------------------------------------------------- live-PS plumbing

_STUBS = (
    "$ErrorActionPreference = 'Stop'\n"
    'function Write-Info { param([string]$Message) Write-Output "INFO:$Message" }\n'
    'function Write-Success { param([string]$Message) Write-Output "OK:$Message" }\n'
    'function Write-Warn { param([string]$Message) Write-Output "WARN:$Message" }\n'
    'function Write-Err { param([string]$Message) Write-Output "ERR:$Message" }\n'
)


def _run_ps(body: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    prelude = _STUBS + "".join(_function_text(n) + "\n" for n in (
        "Test-InstallConsoleUsable",
        "Resolve-DataDir",
        "Apply-DataDirChoice",
        "Set-DataDirChoice",
    ))
    ps1 = Path(tempfile.mkdtemp(prefix="plobi-ps-")) / "probe.ps1"
    ps1.write_text(prelude + body, encoding="utf-8")

    try:
        return subprocess.run(
            [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            capture_output=True,
            text=True,
            input=stdin,
            timeout=180,
        )
    finally:
        shutil.rmtree(ps1.parent, ignore_errors=True)


def _resolve(candidate: str, policy_file: str = "") -> dict:
    """Run the real Resolve-DataDir for one candidate and read its object back off stdout."""
    q = chr(39)
    proc = _run_ps(
        f"$r = Resolve-DataDir -Candidate {q}{candidate}{q} -PolicyFile {q}{policy_file}{q}\n"
        'Write-Output "OK:$($r.ok)"\nWrite-Output "PATH:$($r.path)"\nWrite-Output "REASON:$($r.reason)"\n'
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep and key not in fields:
            fields[key] = value

    return {"ok": fields.get("OK") == "True", "path": fields.get("PATH", ""), "reason": fields.get("REASON", "")}


# ----------------------------------------------------------- two real, opposite places

_SYSTEM_LETTER = os.environ["SystemRoot"][0].upper()


def _writable_on_the_system_drive() -> Path:
    """一定可写、又一定在系统盘上——这样「被挡」只能是盘规在说话。`%LOCALAPPDATA%` 正是旧默认值所在。”"""
    target = Path(os.environ["LOCALAPPDATA"]) / f"plobi-datadir-test-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _writable_off_the_system_drive() -> Path:
    for letter in string.ascii_uppercase:
        if letter == _SYSTEM_LETTER:
            continue
        root = Path(f"{letter}:\\")
        if not root.is_dir():
            continue
        for base in (root / "Temp", root):
            try:
                target = Path(tempfile.mkdtemp(prefix=f"plobi-datadir-test-", dir=str(base)))
            except OSError:
                continue
            return target
    raise AssertionError("no non-system drive")


@pytest.fixture()
def home() -> Path:
    """A writable directory on a drive that is NOT the OS volume, removed afterwards."""
    try:
        target = _writable_off_the_system_drive()
    except AssertionError:
        pytest.skip("这台只有一个盘，「非系统盘落点」在这里不构成——不拿跳过当通过")

    yield target / "plobi"

    shutil.rmtree(target, ignore_errors=True)


@pytest.fixture()
def system_dir() -> Path:
    target = _writable_on_the_system_drive()

    yield target

    shutil.rmtree(target, ignore_errors=True)


# ------------------------------------------------------------------ source contracts


def test_one_question_one_validator_and_no_second_writer_to_the_registry():
    """选定值只写回 `$PlobiHome`，HKCU 那一处照旧读它——本刀不长出第二处解析或第二次钉。"""
    choice = _function_body("Set-DataDirChoice")
    apply_ = _function_body("Apply-DataDirChoice")

    assert "$script:PlobiHome = $Resolved.path" in apply_, "落点必须写回那一个变量"
    assert "$default = $PlobiHome" in choice, "问题从既有参数的默认值起，不另起一套默认"
    assert "SetEnvironmentVariable" not in choice + apply_, "这里不许新建第二处 HKCU 写入"
    assert '[Environment]::SetEnvironmentVariable("PLOBI_HOME", $PlobiHome, "User")' in _SOURCE, (
        "既有那处必须还在原处读 $PlobiHome——判据②要的是复用它的机制，不是旁边再加一条"
    )


def test_the_ask_happens_before_anything_creates_a_directory():
    """唯一入口：信息型早退之后、任何 stage / Main 之前——它们才是 `$PlobiHome` 最早的消费者。"""
    dispatch = _SOURCE.index("# Where the run data goes")
    region = _SOURCE[dispatch:]
    ask = region.index("Set-DataDirChoice")
    stage = region.index('if ($PSBoundParameters.ContainsKey("Stage"))')
    main_call = region.index("\n    Main\n")

    assert ask < stage < main_call, "问与校验必须早于 stage 分派，两条路才共用同一处解析"
    assert not re.search(r"New-Item[^\n]*\$PlobiHome", region), (
        "分派段里不许自己建 $PlobiHome 下的目录——建目录只发生在 stage / Main 里，那都在问题之后"
    )


def test_no_hard_coded_drive_letter_in_the_data_dir_logic():
    """裁定 48.1 / 79：盘符不写死。系统盘那条只在**这台找得到盘规文件**时生效。"""
    for name in ("Resolve-DataDir", "Set-DataDirChoice", "Apply-DataDirChoice", "Test-InstallConsoleUsable"):
        assert not re.search(r"[A-Za-z]:[\\/]", _function_body(name)), f"{name} 里出现了字面盘符路径"

    body = _function_body("Resolve-DataDir")
    assert "Get-PSDrive -PSProvider FileSystem" in body, "盘规靠扫根目录发现，而不是背一个盘符出来"
    assert "$env:SystemRoot" in body, "系统盘从 SystemRoot 推，不写死字母"


def test_non_interactive_still_prints_and_still_refuses():
    """非交互 ≠ 静默：不问，但照旧校验、照旧打印、照旧交回那处钉；不合规当场非零退出。"""
    body = _function_body("Set-DataDirChoice")
    gate = body.index("if ($NonInteractive -or -not (Test-InstallConsoleUsable))")
    silent_branch = body[gate : body.index("while ($true)")]

    assert "Read-Host" not in silent_branch, "非交互那一支不许 Read-Host（会挂在没有控制台的驱动上）"
    assert "exit 1" in silent_branch, '不合规要当场非零退出，不许"先装上再说"'
    assert "数据目录（未询问" in silent_branch, "非交互也要把落点打印出来"
    assert "Read-Host" in body[body.index("while ($true)") :], "交互那一支必须真的问到"


# --------------------------------------------------------------------- live execution


def test_compliant_directory_passes_and_creates_nothing(home: Path):
    r = _resolve(str(home / "not-yet"), policy_file=str(home.parent / "absent-policy.md"))

    assert r["ok"] is True, r["reason"]
    assert Path(r["path"]).resolve() == (home / "not-yet").resolve()
    assert not home.exists(), "Resolve-DataDir 只做决定，不许替安装步骤把目录建出来"


def test_four_refusals_carry_four_different_reasons(home: Path, system_dir: Path):
    policy = home.parent / "directory-policy.md"
    policy.write_text("# 测试造的盘规，只为让这一条判据在场\n", encoding="utf-8")
    absent = str(home.parent / "absent-policy.md")

    empty = _resolve("", policy_file=absent)

    blocked = home.parent / "afile"
    blocked.write_text("x", encoding="utf-8")
    unwritable = _resolve(str(blocked / "sub" / "home"), policy_file=absent)

    cloud = home.parent / "syncroot"
    cloud.mkdir()
    synced = _resolve(str(cloud / "plobi"), policy_file=absent)  # OneDrive 见下一条：需要真环境变量在场
    missing_drive = _resolve(f"Z:\\nope\\plobi" if _SYSTEM_LETTER != "Z" else "Y:\\nope\\plobi", policy_file=absent)

    on_system = _resolve(str(system_dir), policy_file=str(policy))

    assert empty["ok"] is False and "空路径" in empty["reason"], empty
    assert unwritable["ok"] is False and "不可写" in unwritable["reason"], unwritable
    assert missing_drive["ok"] is False and "盘根本不存在" in missing_drive["reason"], missing_drive
    assert on_system["ok"] is False and "系统盘" in on_system["reason"], on_system

    reasons = {empty["reason"], unwritable["reason"], missing_drive["reason"], on_system["reason"]}
    assert len(reasons) == 4, f"每条不合规必须给可行动的不同理由，不许一团 NO：{reasons}"
    assert synced["ok"] is True, "没有同步盘变量在场时不该乱挡（同步那条另有专测）"


def test_a_sync_drive_is_refused_for_what_it_is(home: Path):
    cloud = home.parent / "syncroot"
    cloud.mkdir()
    q = chr(39)
    proc = _run_ps(
        f"$env:OneDrive = {q}{cloud}{q}\n"
        f"$r = Resolve-DataDir -Candidate {q}{cloud / 'plobi'}{q} -PolicyFile {q}{home.parent / 'absent.md'}{q}\n"
        'Write-Output "OK:$($r.ok)"\nWrite-Output "REASON:$($r.reason)"\n'
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK:False" in proc.stdout and "同步盘目录" in proc.stdout, proc.stdout
    assert str(cloud) in proc.stdout, "拒的时候要点名是哪一层同步根，用户才知道改哪儿"


def test_the_system_drive_rule_is_not_granted_to_machines_without_a_policy(system_dir: Path):
    """同一个系统盘路径，没有盘规文件在场就不该被这条挡住——那条规矩是这台写的，不是物理定律。"""
    r = _resolve(str(system_dir), policy_file=str(system_dir.parent / "absent-policy.md"))

    assert r["ok"] is True, r["reason"]


def test_the_policy_is_discovered_by_scanning_roots(home: Path, system_dir: Path):
    """省略 -PolicyFile 时自己去扫根目录：这台真有一份盘规 ⇒ 系统盘必被拒；没有就整条跳过（不假绿）。"""
    found = next((f"{letter}:\\_POLICY\\directory-policy.md" for letter in string.ascii_uppercase
                  if os.path.isfile(f"{letter}:\\_POLICY\\directory-policy.md")), None)
    if not found:
        pytest.skip("这台没有 _POLICY\\directory-policy.md，这条判据在此不构成——不拿跳过当通过")

    r = _resolve(str(system_dir))
    assert r["ok"] is False and "系统盘" in r["reason"], r
    assert "禁止残留系统盘" in r["reason"], f"拒时要说清凭据：{r['reason']}"

    off = _resolve(str(home / "off-system"))
    assert off["ok"] is True, off["reason"]


def test_non_interactive_bad_home_exits_nonzero_and_says_why(home: Path):
    blocked = home.parent / "afile"
    blocked.write_text("x", encoding="utf-8")
    q = chr(39)
    proc = _run_ps(
        f"$PlobiHome = {q}{blocked / 'sub' / 'home'}{q}\n"
        "$NonInteractive = $true\n"
        "$InstallDirWasGiven = $false\n"
        "Set-DataDirChoice\n"
        'Write-Output "UNREACHABLE"\n'
    )

    assert proc.returncode == 1, f"不合规要非零退出：rc={proc.returncode}\n{proc.stdout}{proc.stderr}"
    assert "数据目录不合规" in proc.stdout and "不可写" in proc.stdout, proc.stdout
    assert "UNREACHABLE" not in proc.stdout, "exit 之后不许继续装"


def test_chosen_home_wins_and_install_dir_follows_it_unless_given(home: Path):
    q = chr(39)
    common = (
        f"$PlobiHome = {q}{home}{q}\n$NonInteractive = $true\n"
        f"$InstallDir = {q}{home.parent / 'elsewhere'}{q}\n"
    )
    tail = 'Set-DataDirChoice\nWrite-Output "HOMES:$PlobiHome|$InstallDir"\n'

    derived = _run_ps(common + "$InstallDirWasGiven = $false\n" + tail)
    given = _run_ps(common + "$InstallDirWasGiven = $true\n" + tail)

    assert derived.returncode == 0, derived.stdout + derived.stderr
    last = derived.stdout.strip().splitlines()[-1].removeprefix("HOMES:")
    chosen, _, install = last.partition("|")
    assert Path(chosen).resolve() == home.resolve(), derived.stdout
    assert Path(install).resolve() == (home / "plobi-agent").resolve(), (
        f"数据目录挪了、代码检出还留在系统盘就是半选状态：{install}"
    )
    assert "数据目录（未询问" in derived.stdout, "非交互也要把落点打印出来"

    assert given.returncode == 0, given.stdout + given.stderr
    assert given.stdout.strip().splitlines()[-1].partition("|")[2] == str(home.parent / "elsewhere"), (
        "调用方显式给了 -InstallDir 就不许覆盖它"
    )


def test_the_prompt_asks_once_and_a_typed_answer_is_what_lands(home: Path):
    """判据①：交互安装**必问**，默认值只是建议不是落点。

    只把「此刻有没有可用控制台」那一句替成 $true——绕开的是终端探测，
    `Read-Host` 和它读的 stdin 是真的，答案也确实从键盘那条路上进来。
    """
    typed = home.parent / "typed-instead"
    q = chr(39)
    proc = _run_ps(
        "function Test-InstallConsoleUsable { return $true }\n"
        f"$PlobiHome = {q}{home}{q}\n$NonInteractive = $false\n$InstallDirWasGiven = $false\n"
        'Set-DataDirChoice\nWrite-Output "FINAL:$PlobiHome|$InstallDir"\n',
        stdin=str(typed) + "\n",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "建议：" in proc.stdout, f"没问到就算不过：{proc.stdout}"
    final = proc.stdout.strip().splitlines()[-1].removeprefix("FINAL:")
    chosen, _, install = final.partition("|")
    assert Path(chosen).resolve() == typed.resolve(), f"打进去的路径要真成为落点，不能被默认值吃掉：{proc.stdout}"
    assert Path(install).resolve() == (typed / "plobi-agent").resolve(), proc.stdout
    assert "PLOBI_HOME" in proc.stdout, "钉进 HKCU 那句既有说明要在场，用户才知道自启读的是它"
