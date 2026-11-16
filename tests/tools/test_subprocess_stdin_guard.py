"""Verify that TUI-context subprocess calls specify stdin=.

This is the pytest wrapper for scripts/check_subprocess_stdin.py.
It runs as part of the test suite so CI catches regressions when new
subprocess calls are added without stdin=subprocess.DEVNULL.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_subprocess_stdin.py"

#: 判据（`scripts/check_subprocess_stdin.py`）**看得到字面调用、看不见 kwargs**，
#: 于是有几处它会误报；另有一处是**真的漏了**、产品那格还没补。逐条记在册，
#: 判据的牙只对**新的**违规咬：
#:
#: * `agent/external_offload.py` ×1 —— 那是**文档字符串里的一句散文**
#:   （"为什么不用 `subprocess.run(timeout=…)`"），不是调用。
#: * `tools/browser_launcher.py` ×1、`tools/browser_exec_runtime.py` ×1 ——
#:   `subprocess.Popen(cmd, **kwargs)`，而 `stdin=subprocess.DEVNULL` 在 **kwargs** 里
#:   （browser_launcher.py:241-245 / browser_exec_runtime.py:209-218）。
#: * `tools/source_tools.py` ×1 —— 11-16 `WP-SOURCE-PACKAGES` 新加的那把起
#:   `plobi collectors` 时没带 `stdin=subprocess.DEVNULL`，那个孩子会攥着网关的 stdin
#:   （#14036 那个形状）。**产品那格已补**（`stdin=subprocess.DEVNULL` 加上去了），
#:   这一条已从册子里撤掉。
_KNOWN_FINDINGS = {
    "agent/external_offload.py": 1,
    "tools/browser_exec_runtime.py": 1,
    "tools/browser_launcher.py": 1,
}

#: **Windows 上多出来**的那几条：判据的 `KNOWN_SAFE` 是用正斜杠写的，
#: 而它比对的是 `str(py_file.relative_to(repo_root))` —— Windows 上那是**反斜杠**
#: ⇒ `plugins/security-guidance/patterns.py`（判据自己写着"里面的 subprocess 只是提醒文案"）
#: 在这台机器上没被跳过。**判据那格已改成平台无关**（`relative_to(...).as_posix()`），
#: 两个平台的点名一样 ⇒ 这一份名单撤掉了。
_WINDOWS_ONLY_FINDINGS: dict[str, int] = {}


def _load_guard():
    spec = importlib.util.spec_from_file_location("_stdin_guard", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _flagged_counts(stdout: str) -> dict[str, int]:
    """判据点名的档与次数（输出形如 ``  <路径>:<行>: <片段>``）。路径统一成正斜杠。"""
    counts: dict[str, int] = {}
    for line in stdout.splitlines():
        m = re.match(r"^\s+(\S+):\d+:", line)
        if not m:
            continue
        rel = m.group(1).replace("\\", "/")
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def test_all_tui_subprocess_calls_have_stdin():
    """Every subprocess.run/Popen in TUI-context code must set stdin=.

    判据点名的每一处都记在册（`_KNOWN_FINDINGS`）：多一处红、少了要删——
    名单跟着现况走，不许留着当装饰。
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    expected = dict(_KNOWN_FINDINGS)
    if os.name == "nt":
        expected.update(_WINDOWS_ONLY_FINDINGS)

    counts = _flagged_counts(result.stdout)
    unexpected = sorted(set(counts) - set(expected))
    assert not unexpected, (
        "判据新点名的 subprocess 调用（缺 stdin=）："
        + ", ".join("%s×%d" % (f, counts[f]) for f in unexpected)
        + "。正解是加 `stdin=subprocess.DEVNULL`；确有理由就在那一行写 `# noqa: subprocess-stdin`。"
    )
    drifted = sorted(
        f for f in expected if counts.get(f, 0) != expected[f]
    )
    assert not drifted, (
        "这几处的条数与记录的对不上（判据改了，或那一格被修了）："
        + ", ".join("%s：记录 %d、现在 %d" % (f, expected[f], counts.get(f, 0)) for f in drifted)
        + "。对上了就把记录里对应的那一行改掉再签。"
    )


def test_oauth_setup_token_keeps_inherited_stdin():
    """The interactive 'claude setup-token' login must NOT be muzzled.

    Forcing stdin=subprocess.DEVNULL here would feed the OAuth prompt EOF and
    break interactive token setup. A blanket DEVNULL sweep over TUI-context
    subprocess calls must leave this one inheriting stdin. Regression guard for
    the over-application caught while salvaging the stdin-EOF fix.
    """
    src = (REPO_ROOT / "agent" / "anthropic_adapter.py").read_text()
    assert 'subprocess.run([claude_path, "setup-token"])' in src, (
        "interactive setup-token call changed shape; re-verify it still "
        "inherits stdin (no stdin=subprocess.DEVNULL)"
    )
    assert 'subprocess.run([claude_path, "setup-token"], stdin' not in src, (
        "setup-token must inherit stdin so the user can complete the OAuth "
        "login prompt; do not add stdin=subprocess.DEVNULL"
    )


def test_inline_noqa_marker_exempts_a_call():
    """The guard honors an inline 'noqa: subprocess-stdin' exemption marker."""
    guard = _load_guard()
    flagged = guard.find_subprocess_calls(
        "import subprocess\nsubprocess.run(['ls'])\n", "x.py"
    )
    assert len(flagged) == 1, "unmarked missing-stdin call should be flagged"

    exempt = guard.find_subprocess_calls(
        "import subprocess\nsubprocess.run(['ls'])  # noqa: subprocess-stdin\n",
        "x.py",
    )
    assert exempt == [], "inline marker should exempt the call"

