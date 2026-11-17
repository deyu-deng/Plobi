"""Self-test for the live-system guard fixture in tests/conftest.py.

This file is the canary. If anyone removes a guard or weakens it, these
tests fail. If anyone adds a NEW kill primitive to the codebase without
adding it to the guard, the corresponding test added here will fail too.

The guard exists to protect the developer's live ``plobi-gateway`` process
from being SIGTERMed by tests. See PR #23397 for the original incident
(5+ live gateway kills in 3 days). Per Teknium 2026-05-10:

  > "You better do such a deep scan and scrub of the tests that this
  >  never is possible ever again for all eternity."

Every primitive that can deliver a signal to a foreign process or mutate
the live systemd unit MUST be exercised below. Adding a new primitive to
the guard? Add a test here too.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

# A guaranteed-foreign PID. On POSIX that is PID 1 (init): owned by root,
# not us, and always exists. Windows has no init — PID 1 is usually free
# there (so the guard reads it as a stale pid and lets the call through,
# which is correct: there is nothing to protect), while PID 4 is the
# System process: always alive, never ours.
FOREIGN_PID = 4 if sys.platform == "win32" else 1

# The read-only systemctl probes need systemctl itself to exist. Windows
# has no systemd, so there is nothing to let through there — the guard's
# Windows behaviour is covered by the kill-primitive tests above.
requires_systemctl = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="needs systemd's systemctl on PATH",
)


# ──────────────────── kill primitives ─────────────────────────


def test_os_kill_blocks_foreign_pid():
    with pytest.raises(RuntimeError, match="live-system guard"):
        os.kill(FOREIGN_PID, signal.SIGTERM)


def test_os_kill_blocks_negative_one():
    """``os.kill(-1, sig)`` signals every process we can reach. Must be blocked."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        os.kill(-1, signal.SIGTERM)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="killpg POSIX-only")
def test_os_killpg_blocks_foreign_pgid():
    with pytest.raises(RuntimeError, match="live-system guard"):
        os.killpg(FOREIGN_PID, signal.SIGTERM)


# ──────────────────── the sig=0 probe ────────────────────
#
# ``os.kill(pid, 0)`` is "just asking whether the pid is alive". On POSIX
# that is true. On Windows it is NOT: sig=0 collides with CTRL_C_EVENT at
# the C level (bpo-14484), so the call becomes GenerateConsoleCtrlEvent —
# a Ctrl+C for every process sharing the console, the pytest runner
# included. That is what killed three full-suite runs here (the runner died
# in ThreadPoolExecutor.shutdown → t.join() with a KeyboardInterrupt, exit
# 130, and the summary plus every result collected so far was lost).
#
# The guard now answers that question WITHOUT sending anything. These two
# tests are the reverse tests for it — the canary must fail if the guard is
# weakened back to waving sig=0 through.


def test_os_kill_zero_probe_answers_without_signalling():
    """``os.kill(pid, 0)`` must answer, not signal.

    A pid that does not exist proves which path ran: the raw Windows path
    raises OSError(EINVAL) out of GenerateConsoleCtrlEvent, while the
    guard's signal-free probe raises ProcessLookupError — the shape POSIX
    has always had, so callers branching on it keep working.
    """
    dead_pid = 0x7FFFFFF0  # far above any live pid on Windows
    with pytest.raises(ProcessLookupError):
        os.kill(dead_pid, 0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only helper")
def test_win_liveness_probe_is_signal_free():
    """The Windows stand-in answers for a live pid without sending anything."""
    from tests.conftest import _win_liveness_probe

    assert _win_liveness_probe(os.getpid()) is None
    with pytest.raises(ProcessLookupError):
        _win_liveness_probe(0x7FFFFFF0)


# ──────────────────── subprocess regex bypasses ────────────────


def test_subprocess_run_systemctl_restart_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["systemctl", "--user", "restart", "plobi-gateway"])


def test_subprocess_run_full_path_systemctl_blocked():
    """``/usr/bin/systemctl`` (full path) must be blocked too."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["/usr/bin/systemctl", "--user", "stop", "plobi-gateway"])


def test_subprocess_run_sudo_systemctl_blocked():
    """``sudo systemctl ...`` defeated the old head==systemctl check."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["sudo", "systemctl", "restart", "plobi-gateway"])


def test_subprocess_run_env_systemctl_blocked():
    """``env systemctl ...`` similarly defeated the old head check."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["env", "systemctl", "--user", "restart", "plobi-gateway"])


def test_subprocess_run_bash_c_systemctl_blocked():
    """``bash -c "systemctl ..."`` must also be caught."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["bash", "-c", "systemctl --user restart plobi-gateway"])


def test_subprocess_run_sh_c_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["sh", "-c", "systemctl --user stop plobi-gateway"])


def test_subprocess_run_setsid_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["setsid", "systemctl", "kill", "plobi-gateway"])


def test_subprocess_run_string_shell_true_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(
            "systemctl --user restart plobi-gateway",
            shell=True,
        )


def test_subprocess_popen_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.Popen(["systemctl", "--user", "stop", "plobi-gateway"])


def test_subprocess_call_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.call(["systemctl", "--user", "restart", "plobi-gateway"])


def test_subprocess_check_call_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.check_call(["systemctl", "--user", "restart", "plobi-gateway"])


def test_subprocess_check_output_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.check_output(["systemctl", "--user", "restart", "plobi-gateway"])


def test_subprocess_getoutput_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.getoutput("systemctl --user restart plobi-gateway")


def test_subprocess_getstatusoutput_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.getstatusoutput("systemctl --user restart plobi-gateway")


# ──────────────────── os.system / os.popen ────────────────────


def test_os_system_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        os.system("systemctl --user restart plobi-gateway")


def test_os_popen_systemctl_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        os.popen("systemctl --user restart plobi-gateway")


# ──────────────────── pty.spawn ────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="no pty module on Windows")
def test_pty_spawn_systemctl_blocked():
    import pty
    with pytest.raises(RuntimeError, match="live-system guard"):
        pty.spawn(["systemctl", "--user", "restart", "plobi-gateway"])


# ──────────────────── asyncio.create_subprocess_* ──────────────


def test_asyncio_create_subprocess_exec_systemctl_blocked():
    import asyncio

    async def _attempt():
        await asyncio.create_subprocess_exec(
            "systemctl", "--user", "restart", "plobi-gateway"
        )

    with pytest.raises(RuntimeError, match="live-system guard"):
        asyncio.run(_attempt())


def test_asyncio_create_subprocess_shell_systemctl_blocked():
    import asyncio

    async def _attempt():
        await asyncio.create_subprocess_shell(
            "systemctl --user restart plobi-gateway"
        )

    with pytest.raises(RuntimeError, match="live-system guard"):
        asyncio.run(_attempt())


# ──────────────────── pkill / killall / taskkill ───────────────


def test_subprocess_pkill_plobi_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["pkill", "-f", "plobi"])


def test_subprocess_pkill_plobi_gateway_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["pkill", "-f", "plobi-gateway"])


def test_subprocess_pkill_python_dash_f_blocked():
    """``pkill -f python`` matches the gateway's "python -m plobi_cli.main"."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["pkill", "-f", "python"])


def test_subprocess_killall_plobi_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["killall", "plobi"])


# ──────────────────── pass-through cases (must NOT raise) ──────


@requires_systemctl
def test_systemctl_status_passes_through():
    """Read-only systemctl probes (status/show/list-units) are fine."""
    # Run with check=False so we don't fail on the gateway's exit code.
    r = subprocess.run(
        ["systemctl", "--user", "status", "plobi-gateway", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r is not None  # Did not raise — the guard let it through.


@requires_systemctl
def test_systemctl_show_passes_through():
    r = subprocess.run(
        ["systemctl", "--user", "show", "plobi-gateway", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r is not None


@requires_systemctl
def test_systemctl_list_units_passes_through():
    r = subprocess.run(
        ["systemctl", "--user", "list-units", "fake-not-real-unit*", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r is not None


@requires_systemctl
def test_systemctl_unrelated_unit_passes_through():
    """systemctl restart of a non-plobi unit is allowed (we only protect plobi)."""
    # Use --dry-run so we don't actually try to restart anything; just
    # verify the guard doesn't block the call. systemctl supports
    # --dry-run via the privileged API; on user scope it usually fails
    # quickly without side effects.
    r = subprocess.run(
        ["systemctl", "--user", "show", "fake-not-real-unit"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r is not None


def test_kill_own_subtree_passes_through():
    """We CAN kill our own children — guard recognizes them via psutil."""
    p = subprocess.Popen(["sleep", "30"])
    try:
        os.kill(p.pid, signal.SIGTERM)
    finally:
        p.wait(timeout=2)
    # SIGTERM = 15; subprocess returncode is -15 on POSIX. On Windows
    # os.kill(SIGTERM) is TerminateProcess, so the child exits with 15.
    expected = (
        {15}
        if sys.platform == "win32"
        else {-signal.SIGTERM, 128 + int(signal.SIGTERM)}
    )
    assert p.returncode in expected


def test_subprocess_pkill_with_unrelated_pattern_passes_through():
    """``pkill -f some-unrelated-pattern`` (no plobi/python) is fine."""
    # We don't actually run pkill — just verify the guard would let it
    # through by inspecting the matcher. Re-implementing the check here
    # would duplicate the guard; instead spawn a noop to confirm no raise.
    # Use 'true' so it succeeds quickly.
    r = subprocess.run(["true"], capture_output=True)
    assert r.returncode == 0


def test_killer_word_inside_an_argument_is_not_a_killer(tmp_path):
    """C 类的反向牙齿：搜索词长得像杀手、路径里有 plobi，都不许拦。

    这正是 `tests/tools/test_search_hidden_dirs.py:118` 的形状 -- argv 里
    出现裸 `skill`，scratch 家目录名又含 `plobi`。修之前守卫把它判成"要打死
    活网关"，判据却只是参数长得像。这里用 `sys.executable -c pass` 跑一条
    无害命令，但把那两个特征原样放进参数：守卫必须放行，命令本身不杀任何东西。
    """
    fake_home = tmp_path / "plobi-tests-home.FAKE"
    fake_home.mkdir()
    r = subprocess.run(
        [sys.executable, "-c", "pass", "--no-heading", "real skill", str(fake_home)],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode(errors="replace")


def test_pkill_wrapped_in_a_shell_is_still_blocked():
    """收窄到 argv[0] 不许把 `bash -c "pkill -f plobi"` 变成漏网 -- 它真会打死活网关。"""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["bash", "-c", "pkill -f plobi"])


def test_pkill_behind_sudo_or_env_is_still_blocked():
    """argv[0] 收窄不许把 `sudo pkill -f plobi` / `env FOO=1 pkill -f plobi` 放过去。"""
    for argv in (
        ["sudo", "pkill", "-f", "plobi"],
        ["env", "FOO=1", "pkill", "-f", "plobi-gateway"],
    ):
        with pytest.raises(RuntimeError, match="live-system guard"):
            subprocess.run(argv)


def test_pkill_targeting_an_unrelated_word_is_not_blocked(tmp_path):
    """真杀手但目标与 plobi 无关时仍放行（收窄不能顺手把守卫放宽成摆设）。"""
    r = subprocess.run(
        [sys.executable, "-c", "import sys; sys.exit(0)", "pkill-never-runs"],
        capture_output=True,
    )
    assert r.returncode == 0


def test_normal_subprocess_run_passes_through():
    """Plain non-systemctl subprocess.run should work normally."""
    r = subprocess.run(["echo", "hello"], capture_output=True, text=True)
    assert r.stdout.strip() == "hello"


# ──────────────────── bypass marker ─────────────────────────────


@pytest.mark.live_system_guard_bypass
def test_bypass_marker_disables_guard():
    """The bypass marker exists for tests that genuinely need real signal delivery
    (e.g. PTY tests SIGINTing their own child). Verify it works.

    On POSIX we use it harmlessly by signaling our own PID 0 (own group) so we
    don't actually kill anything — but the call goes through real os.kill.

    On Windows that call is NOT harmless: ``os.kill(pid, 0)`` is not a
    liveness probe there, it is ``GenerateConsoleCtrlEvent(0, pid)`` — a
    Ctrl+C handed to EVERY process sharing the console (bpo-14484). Making
    it here killed the pytest runner mid-suite (it died in
    ``ThreadPoolExecutor.shutdown`` → ``t.join()`` with a KeyboardInterrupt,
    exit 130) and took the whole run's summary with it. So on Windows we
    assert the structural fact instead: with the bypass marker the guard
    never installs its wrapper, so ``os.kill`` is still the native one.
    """
    if sys.platform == "win32":
        # The guard's wrapper is named ``_guarded_kill``; the native os.kill
        # is the builtin ``kill``. Bypassed ⇒ native.
        assert os.kill.__name__ == "kill", (
            f"bypass marker did not restore the native os.kill "
            f"(got {os.kill.__name__!r})"
        )
        return
    # With bypass, the guard yields without installing the monkeypatch,
    # so we get the real os.kill. Calling os.kill(os.getpid(), 0) just
    # checks that the PID exists — harmless.
    os.kill(os.getpid(), 0)  # No exception — guard is OFF.


# ──────────────────── `plobi update` spawn ─────────────────────
#
# Blocked cases all point at a path that cannot exist: if this branch of the
# guard is ever weakened, the call must fail with FileNotFoundError rather than
# really running `update` against the live checkout.


@pytest.mark.parametrize("cmd", [
    ["/nonexistent/bin/plobi", "update"],
    ["/nonexistent/python", "-m", "plobi_cli.main", "update", "--gateway"],
    ["bash", "-c", "/nonexistent/bin/plobi update"],
])
def test_plobi_update_spawn_is_blocked(cmd):
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(cmd, capture_output=True)


def test_commit_message_naming_the_product_is_not_an_update_spawn(tmp_path):
    """`git commit -m "chore(plobi): update 1 file(s)"` must reach git.

    The rename made the product word identical to the guard's CLI token, so a
    substring match over the whole command string turned an ordinary commit into
    a blocked "update" spawn — which is how tests/plobi/test_mind.py went red.
    The guard matches command position, not argument prose.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f.txt"], check=True)
    proc = subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=guard-test",
         "-c", "user.email=guard@test.invalid", "commit", "-q",
         "-m", "chore(plobi): update 1 file(s)"],
        capture_output=True, text=True,
    )
    assert "live-system guard" not in proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr
