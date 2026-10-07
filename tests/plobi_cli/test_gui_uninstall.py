"""Tests for plobi_cli.gui_uninstall — GUI-only uninstall + install discovery.

Covers the cross-platform artifact discovery, the agent/GUI detection the
desktop UI gates options on, and that ``uninstall_gui`` removes only GUI
artifacts (built renderer/release/node_modules, packaged bundle, Electron
userData) while leaving the Python agent + config/sessions/.env intact.
"""

import sys
from pathlib import Path

import pytest

import plobi_cli.gui_uninstall as gu


def _make_agent(plobi_home: Path) -> Path:
    """Create a fake agent install: source package + venv."""
    agent_root = plobi_home / "plobi-agent"
    (agent_root / "plobi_cli").mkdir(parents=True)
    (agent_root / "plobi_cli" / "__init__.py").write_text("")
    (agent_root / "venv" / "bin").mkdir(parents=True)
    return agent_root


def _make_gui_build(plobi_home: Path) -> None:
    """Create the source-built GUI artifacts a `plobi desktop` run produces."""
    desktop = plobi_home / "plobi-agent" / "apps" / "desktop"
    (desktop / "dist").mkdir(parents=True)
    (desktop / "dist" / "index.html").write_text("<html>")
    (desktop / "release" / "linux-unpacked").mkdir(parents=True)
    (desktop / "node_modules").mkdir(parents=True)
    (plobi_home / "plobi-agent" / "node_modules").mkdir(parents=True)
    (plobi_home / "desktop-build-stamp.json").write_text("{}")


def _make_user_data(plobi_home: Path) -> None:
    (plobi_home / "config.yaml").write_text("x: 1\n")
    (plobi_home / ".env").write_text("KEY=secret\n")
    (plobi_home / "sessions").mkdir()


def test_agent_is_installed_detects_source_and_venv(tmp_path):
    plobi_home = tmp_path / ".plobi"
    plobi_home.mkdir()
    assert gu.agent_is_installed(plobi_home) is False
    _make_agent(plobi_home)
    assert gu.agent_is_installed(plobi_home) is True


def test_agent_is_installed_venv_only(tmp_path):
    """A checkout with only a venv (no package dir yet) still counts."""
    plobi_home = tmp_path / ".plobi"
    (plobi_home / "plobi-agent" / "venv").mkdir(parents=True)
    assert gu.agent_is_installed(plobi_home) is True


def test_source_built_artifacts_lists_known_paths(tmp_path):
    plobi_home = tmp_path / ".plobi"
    _make_gui_build(plobi_home)
    artifacts = gu.source_built_gui_artifacts(plobi_home)
    names = {p.name for p in artifacts}
    assert "dist" in names
    assert "release" in names
    assert "node_modules" in names
    assert "desktop-build-stamp.json" in names


def test_gui_is_installed_true_when_built(tmp_path, monkeypatch):
    plobi_home = tmp_path / ".plobi"
    _make_gui_build(plobi_home)
    # Make sure packaged-app + userdata probes don't false-positive on the box
    # running the test.
    monkeypatch.setattr(gu, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu, "desktop_userdata_dir", lambda: tmp_path / "nope")
    assert gu.gui_is_installed(plobi_home) is True


def test_gui_is_installed_false_when_nothing(tmp_path, monkeypatch):
    plobi_home = tmp_path / ".plobi"
    plobi_home.mkdir()
    monkeypatch.setattr(gu, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu, "desktop_userdata_dir", lambda: tmp_path / "nope")
    assert gu.gui_is_installed(plobi_home) is False


def test_uninstall_gui_removes_only_gui_artifacts(tmp_path, monkeypatch):
    """The core invariant: GUI gone, agent + user data untouched."""
    plobi_home = tmp_path / ".plobi"
    agent_root = _make_agent(plobi_home)
    _make_gui_build(plobi_home)
    _make_user_data(plobi_home)

    # Isolate the packaged-app + userdata probes from the test machine.
    monkeypatch.setattr(gu, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu, "desktop_userdata_dir", lambda: tmp_path / "userdata-none")

    removed = gu.uninstall_gui(plobi_home)
    removed_names = {p.name for p in removed}

    # GUI artifacts removed.
    desktop = agent_root / "apps" / "desktop"
    assert not (desktop / "dist").exists()
    assert not (desktop / "release").exists()
    assert not (desktop / "node_modules").exists()
    assert not (agent_root / "node_modules").exists()
    assert not (plobi_home / "desktop-build-stamp.json").exists()
    assert "dist" in removed_names

    # Agent + user data preserved.
    assert (agent_root / "plobi_cli" / "__init__.py").exists()
    assert (agent_root / "venv").exists()
    assert (plobi_home / "config.yaml").exists()
    assert (plobi_home / ".env").exists()
    assert (plobi_home / "sessions").exists()
    # The desktop source dir itself survives (only its build output is gone).
    assert desktop.exists()


def test_uninstall_gui_removes_userdata(tmp_path, monkeypatch):
    plobi_home = tmp_path / ".plobi"
    _make_agent(plobi_home)
    userdata = tmp_path / "Plobi-userdata"
    userdata.mkdir()
    (userdata / "connection.json").write_text("{}")

    monkeypatch.setattr(gu, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu, "desktop_userdata_dir", lambda: userdata)

    gu.uninstall_gui(plobi_home)
    assert not userdata.exists()


def test_uninstall_gui_keeps_userdata_when_requested(tmp_path, monkeypatch):
    plobi_home = tmp_path / ".plobi"
    _make_agent(plobi_home)
    userdata = tmp_path / "Plobi-userdata"
    userdata.mkdir()

    monkeypatch.setattr(gu, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu, "desktop_userdata_dir", lambda: userdata)

    gu.uninstall_gui(plobi_home, remove_userdata=False)
    assert userdata.exists()


def test_uninstall_gui_removes_packaged_bundle(tmp_path, monkeypatch):
    plobi_home = tmp_path / ".plobi"
    _make_agent(plobi_home)
    bundle = tmp_path / "Plobi.app"
    (bundle / "Contents").mkdir(parents=True)

    monkeypatch.setattr(gu, "packaged_gui_app_paths", lambda: [bundle])
    monkeypatch.setattr(gu, "desktop_userdata_dir", lambda: tmp_path / "none")

    removed = gu.uninstall_gui(plobi_home)
    assert not bundle.exists()
    assert bundle in removed


def test_gui_install_summary_shape(tmp_path, monkeypatch):
    plobi_home = tmp_path / ".plobi"
    _make_agent(plobi_home)
    _make_gui_build(plobi_home)
    monkeypatch.setattr(gu, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu, "desktop_userdata_dir", lambda: tmp_path / "none")

    summary = gu.gui_install_summary(plobi_home)
    # JSON-serializable primitives the desktop UI gates on.
    assert summary["agent_installed"] is True
    assert summary["gui_installed"] is True
    assert isinstance(summary["source_built_artifacts"], list)
    assert all(isinstance(p, str) for p in summary["source_built_artifacts"])
    assert summary["plobi_home"] == str(plobi_home)
    assert summary["platform"] == sys.platform


def test_userdata_dir_per_platform(monkeypatch):
    """userData path matches Electron's app.getPath('userData') for "Plobi"."""
    home = Path("/home/tester")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    monkeypatch.setattr(gu.sys, "platform", "darwin")
    assert gu.desktop_userdata_dir() == home / "Library" / "Application Support" / "Plobi"

    monkeypatch.setattr(gu.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert gu.desktop_userdata_dir() == home / ".config" / "Plobi"


def test_userdata_dir_windows(monkeypatch):
    home = Path("/home/tester")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(gu.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\tester\AppData\Roaming")
    assert gu.desktop_userdata_dir() == Path(r"C:\Users\tester\AppData\Roaming") / "Plobi"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_remove_path_handles_symlink(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    assert gu._remove_path(link) is True
    assert not link.exists()
    # The symlink is gone but its target is untouched.
    assert target.exists()


class _Args:
    """Minimal argparse-Namespace stand-in for run_uninstall."""

    def __init__(
        self,
        *,
        yes=False,
        full=False,
        gui=False,
        gui_summary=False,
        purge_user_env=False,
    ):
        self.yes = yes
        self.full = full
        self.gui = gui
        self.gui_summary = gui_summary
        self.purge_user_env = purge_user_env


def test_run_uninstall_yes_keep_data_is_non_interactive(tmp_path, monkeypatch):
    """``--yes`` (no ``--full``) runs with no prompt, sweeps the GUI, keeps data.

    We DO NOT spawn the real CLI here (its project_root removal would delete the
    test checkout) — we call run_uninstall in-process against a throwaway
    PLOBI_HOME with all the destructive externals stubbed out.
    """
    import plobi_cli.uninstall as uninstall

    plobi_home = tmp_path / ".plobi"
    agent_root = plobi_home / "plobi-agent"
    (agent_root / "plobi_cli").mkdir(parents=True)
    (plobi_home / "config.yaml").write_text("x: 1\n")
    desktop = agent_root / "apps" / "desktop"
    (desktop / "release").mkdir(parents=True)
    (plobi_home / "desktop-build-stamp.json").write_text("{}")
    fake_code = tmp_path / "checkout"
    fake_code.mkdir()

    # Stub every destructive external so the test only exercises the control
    # flow + the real GUI sweep (which is safe inside tmp_path).
    monkeypatch.setattr(uninstall, "get_plobi_home", lambda: plobi_home)
    monkeypatch.setattr(uninstall, "get_project_root", lambda: fake_code)
    monkeypatch.setattr(uninstall, "uninstall_gateway_service", lambda: False)
    monkeypatch.setattr(uninstall, "remove_path_from_shell_configs", lambda: [])
    monkeypatch.setattr(uninstall, "remove_wrapper_script", lambda: [])
    monkeypatch.setattr(uninstall, "remove_node_symlinks", lambda h: [])
    monkeypatch.setattr(uninstall, "_discover_named_profiles", lambda: [])
    # Windows-only registry sweeps. These two would delete the REAL
    # HKCU\Environment values (PLOBI_HOME / PLOBI_GIT_BASH_PATH) and edit the
    # user's PATH — running this test file must never touch the user's env.
    monkeypatch.setattr(uninstall, "remove_plobi_env_vars_windows", lambda: [])
    monkeypatch.setattr(uninstall, "remove_path_from_windows_registry", lambda *_a, **_k: [])
    # Make input() blow up so a regression that reaches a prompt fails loudly.
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted in --yes mode"))

    from plobi_cli import gui_uninstall as gu_mod
    monkeypatch.setattr(gu_mod, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu_mod, "desktop_userdata_dir", lambda: tmp_path / "none")

    uninstall.run_uninstall(_Args(yes=True, full=False))

    # Code checkout removed, GUI artifacts swept, but user data preserved.
    assert not fake_code.exists()
    assert not (plobi_home / "desktop-build-stamp.json").exists()
    assert not (desktop / "release").exists()
    assert (plobi_home / "config.yaml").exists()
    assert plobi_home.exists()


def test_run_uninstall_yes_full_wipes_home(tmp_path, monkeypatch):
    """``--yes --full`` removes the whole PLOBI_HOME non-interactively."""
    import plobi_cli.uninstall as uninstall

    plobi_home = tmp_path / ".plobi"
    (plobi_home / "plobi-agent" / "plobi_cli").mkdir(parents=True)
    (plobi_home / "config.yaml").write_text("x: 1\n")
    fake_code = tmp_path / "checkout"
    fake_code.mkdir()

    monkeypatch.setattr(uninstall, "get_plobi_home", lambda: plobi_home)
    monkeypatch.setattr(uninstall, "get_project_root", lambda: fake_code)
    monkeypatch.setattr(uninstall, "uninstall_gateway_service", lambda: False)
    monkeypatch.setattr(uninstall, "remove_path_from_shell_configs", lambda: [])
    monkeypatch.setattr(uninstall, "remove_wrapper_script", lambda: [])
    monkeypatch.setattr(uninstall, "remove_node_symlinks", lambda h: [])
    monkeypatch.setattr(uninstall, "_discover_named_profiles", lambda: [])
    # Windows-only registry sweeps — same reason as above: a test run must
    # never delete the real HKCU\Environment values or edit the user's PATH.
    monkeypatch.setattr(uninstall, "remove_plobi_env_vars_windows", lambda: [])
    monkeypatch.setattr(uninstall, "remove_path_from_windows_registry", lambda *_a, **_k: [])
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted in --yes mode"))

    from plobi_cli import gui_uninstall as gu_mod
    monkeypatch.setattr(gu_mod, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu_mod, "desktop_userdata_dir", lambda: tmp_path / "none")

    uninstall.run_uninstall(_Args(yes=True, full=True))

    assert not plobi_home.exists()


def test_uninstall_module_main_gui_mode(tmp_path, monkeypatch):
    """`python -m plobi_cli.uninstall --mode gui` runs the GUI-only path.

    This is the lightweight, venv-independent entrypoint the desktop launches
    with a system Python (so lite/full don't rmtree their own running venv on
    Windows). Verify it dispatches by mode without prompting.
    """
    import plobi_cli.uninstall as uninstall

    plobi_home = tmp_path / ".plobi"
    agent_root = plobi_home / "plobi-agent"
    (agent_root / "plobi_cli").mkdir(parents=True)
    desktop = agent_root / "apps" / "desktop"
    (desktop / "release").mkdir(parents=True)
    (plobi_home / "desktop-build-stamp.json").write_text("{}")
    (plobi_home / "config.yaml").write_text("x: 1\n")

    monkeypatch.setattr(uninstall, "get_plobi_home", lambda: plobi_home)
    from plobi_cli import gui_uninstall as gu_mod
    monkeypatch.setattr(gu_mod, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu_mod, "desktop_userdata_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(gu_mod, "get_plobi_home", lambda: plobi_home)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted in module main"))

    rc = uninstall.main(["--mode", "gui"])
    assert rc == 0
    # GUI swept, agent + config kept (gui-only contract).
    assert not (desktop / "release").exists()
    assert not (plobi_home / "desktop-build-stamp.json").exists()
    assert (agent_root / "plobi_cli").exists()
    assert (plobi_home / "config.yaml").exists()


def test_uninstall_module_main_rejects_bad_mode():
    """An invalid --mode exits non-zero (argparse), never silently full-wipes."""
    import plobi_cli.uninstall as uninstall

    with pytest.raises(SystemExit) as exc:
        uninstall.main(["--mode", "nuke"])
    assert exc.value.code != 0


def test_uninstall_args_namespace_mode_mapping():
    """_UninstallArgs maps mode → the gui/full flags run_uninstall reads."""
    import plobi_cli.uninstall as uninstall

    gui = uninstall._UninstallArgs(mode="gui")
    assert gui.gui is True and gui.full is False and gui.yes is True

    lite = uninstall._UninstallArgs(mode="lite")
    assert lite.gui is False and lite.full is False and lite.yes is True

    full = uninstall._UninstallArgs(mode="full")
    assert full.gui is False and full.full is True and full.yes is True
    # The desktop launches this module entrypoint, so a routine "uninstall the
    # app" click must never imply a User-environment wipe (裁定 75).
    assert full.purge_user_env is False


class _FakeKeyHandle:
    """What ``winreg.OpenKey`` hands back: usable as a context manager, no-op body."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def Close(self):  # noqa: N802 — some callers close explicitly
        return None


# What the Windows installer wrote for itself, plus one bystander that belongs to
# somebody else. The assertions below are written as *relations* against these,
# not as a snapshot of the uninstall's cleanup list: that list is expected to grow,
# and it may only ever grow with Plobi-owned names.
_PLOBI_OWNED_ENV_NAMES = ("PLOBI_HOME", "PLOBI_GIT_BASH_PATH")
_FOREIGN_ENV_NAME = "UNRELATED_TOOL_HOME"


class _FakeWinreg:
    """Stand-in for the ``winreg`` module: records calls, never touches HKCU.

    ``remove_plobi_env_vars_windows`` imports ``winreg`` *inside* the function, so
    putting this in ``sys.modules`` lets the real code path run on any OS. That is
    the point: "an uninstall leaves the user's environment alone unless asked" is a
    behavior contract, and a contract we only assert on Windows would go untested
    on the machine that broke (and on every CI runner that isn't Windows).
    """

    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    REG_SZ = 1
    # Real winreg exposes these; the code under test passes them to OpenKey, so a
    # fake missing them fails with AttributeError instead of recording the call.
    HKEY_CURRENT_USER = -2147483647  # 0x80000001

    def __init__(self):
        # HKCU\Environment as the uninstall would find it: the values Plobi owns,
        # plus one it must never touch.
        self.values: dict[str, str] = {
            **{name: r"D:\Data\AppData\plobi" for name in _PLOBI_OWNED_ENV_NAMES},
            _FOREIGN_ENV_NAME: r"C:\Users\tester\unrelated-tool",
        }
        self.opened: list[str] = []
        self.deleted: list[str] = []

    def OpenKey(self, root, sub_key, reserved=0, access=0):  # noqa: N802 (winreg spelling)
        self.opened.append(sub_key)
        # Real winreg handles are context managers (`with winreg.OpenKey(...) as k`),
        # so the fake has to be one too — a bare string fails inside the code under
        # test with a TypeError and hides the behavior we are asserting.
        return _FakeKeyHandle()

    def CloseKey(self, handle):  # noqa: N802
        return None

    def QueryValueEx(self, handle, name):  # noqa: N802
        # Real winreg raises FileNotFoundError for a value that isn't there, and the
        # code under test keys its whole delete list off that. Faking it as
        # "everything exists" would let the test pass for a name no installer ever
        # wrote, so the lookup goes through the seeded values.
        try:
            return self.values[name], self.REG_SZ
        except KeyError:
            raise FileNotFoundError(name)

    def DeleteValue(self, handle, name):  # noqa: N802
        self.deleted.append(name)
        self.values.pop(name, None)


def _stub_everything_except_the_registry(monkeypatch, *, plobi_home, fake_code):
    """Stub every destructive external *except* the two Windows registry sweeps.

    Deliberately does **not** patch ``remove_plobi_env_vars_windows`` — that one is
    under test and runs for real against :class:`_FakeWinreg`.
    """
    import plobi_cli.uninstall as uninstall

    monkeypatch.setattr(uninstall, "get_plobi_home", lambda: plobi_home)
    monkeypatch.setattr(uninstall, "get_project_root", lambda: fake_code)
    monkeypatch.setattr(uninstall, "uninstall_gateway_service", lambda: False)
    monkeypatch.setattr(uninstall, "remove_path_from_shell_configs", lambda: [])
    monkeypatch.setattr(uninstall, "remove_wrapper_script", lambda: [])
    monkeypatch.setattr(uninstall, "remove_node_symlinks", lambda h: [])
    monkeypatch.setattr(uninstall, "remove_portable_tooling_windows", lambda h: [])
    monkeypatch.setattr(uninstall, "_discover_named_profiles", lambda: [])
    # The PATH sweep reads and rewrites a multi-string value; that is not the
    # behavior this cut is about, so it is recorded rather than faked in full.
    path_calls: list[object] = []
    monkeypatch.setattr(
        uninstall,
        "remove_path_from_windows_registry",
        lambda home: path_calls.append(home) or [],
    )
    # Force the Windows branch on so this asserts the same way on every OS — via
    # sys.platform, the same trick test_userdata_dir_windows uses, which keeps the
    # real ``_is_windows()`` predicate in the path under test.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted in --yes mode"))

    from plobi_cli import gui_uninstall as gu_mod

    monkeypatch.setattr(gu_mod, "packaged_gui_app_paths", lambda: [])
    monkeypatch.setattr(gu_mod, "desktop_userdata_dir", lambda: Path("<unused>"))
    return path_calls


def _make_throwaway_home(tmp_path):
    plobi_home = tmp_path / ".plobi"
    (plobi_home / "plobi-agent" / "plobi_cli").mkdir(parents=True)
    (plobi_home / "config.yaml").write_text("x: 1\n")
    fake_code = tmp_path / "checkout"
    fake_code.mkdir()
    return plobi_home, fake_code


def test_uninstall_default_never_opens_the_user_environment_key(tmp_path, monkeypatch):
    """默认卸载打开都不打开 HKCU\\Environment —— 不是"打开了但没删"。

    回归的是 2026-10-07 的实际事故：跑一次测试就真删一次 PLOBI_HOME，之后新起的进程
    静默落回 %LOCALAPPDATA%\\plobi，盘上长出第二个家（症状是「左栏只剩日程管家」那三条）。
    """
    import plobi_cli.uninstall as uninstall

    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    plobi_home, fake_code = _make_throwaway_home(tmp_path)
    path_calls = _stub_everything_except_the_registry(
        monkeypatch, plobi_home=plobi_home, fake_code=fake_code
    )

    uninstall.run_uninstall(_Args(yes=True, full=False))

    assert fake.opened == [], f"默认卸载碰了注册表：{fake.opened}"
    assert fake.deleted == [], f"默认卸载删了用户环境变量：{fake.deleted}"
    assert path_calls == [], "默认卸载碰了 User PATH"
    # 家目录指针必须原样在位——就是被抹掉后会长出第二个家的那个值。
    assert fake.values["PLOBI_HOME"] == r"D:\Data\AppData\plobi"
    # 数据仍然照旧保留（本刀不许改的其它卸载行为）
    assert plobi_home.exists()


@pytest.mark.parametrize("full", [False, True])
def test_purge_user_env_flag_is_the_only_way_to_clear_it(tmp_path, monkeypatch, full):
    """带 --purge-user-env 才清；清的只会是 Plobi 自己写下的变量，别人的不碰。

    ``full`` 两种取值都要过：``--full``（删数据）不等于"顺手把用户环境变量抹了"，
    这两件事在裁定 75 之前被混成一件，就是这次事故的形状。
    """
    import plobi_cli.uninstall as uninstall

    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    plobi_home, fake_code = _make_throwaway_home(tmp_path)
    path_calls = _stub_everything_except_the_registry(
        monkeypatch, plobi_home=plobi_home, fake_code=fake_code
    )

    uninstall.run_uninstall(_Args(yes=True, full=full, purge_user_env=True))

    # 开关打开才允许真删（正面断言，不是"没跳过"的反证）。
    assert fake.deleted, "显式 --purge-user-env 必须真的写到 HKCU\\Environment"
    # 断言写成关系而不是键名单快照：卸载的清理名单以后会长，长出的任何东西都只能
    # 是 Plobi 自己写下的变量，别人的一个都不许碰。
    assert set(fake.deleted) <= set(_PLOBI_OWNED_ENV_NAMES), fake.deleted
    assert _FOREIGN_ENV_NAME not in fake.deleted
    assert _FOREIGN_ENV_NAME in fake.values
    # 本刀要守的那一条：家目录指针只有在被明确要求时才被清。
    assert "PLOBI_HOME" in fake.deleted
    assert len(path_calls) == 1, "显式清理时 User PATH 那一趟应当被走到一次"


def test_uninstall_default_says_the_user_env_was_kept(tmp_path, monkeypatch, capsys):
    """跳过必须说出来，不能静默默认。

    必做 2 要求默认不抹时打印一句「保留了用户环境变量（要清请加 --purge-user-env）」。
    静默跳过会让用户以为卸载已经把机器擦干净了，之后 ``PLOBI_HOME`` 缺失长出的第二个家
    就会被算成别的毛病。断言只锁这句话必须同时给到「保留了」和「怎么才能清」，不锁措辞。
    """
    import plobi_cli.uninstall as uninstall

    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    plobi_home, fake_code = _make_throwaway_home(tmp_path)
    _stub_everything_except_the_registry(
        monkeypatch, plobi_home=plobi_home, fake_code=fake_code
    )

    uninstall.run_uninstall(_Args(yes=True, full=True))

    out = capsys.readouterr().out
    # 得告诉用户怎么才会清——否则这个开关等于不存在。
    assert "--purge-user-env" in out, out
    # 而且不能一面跳过一面报告已清理。
    assert "Removed User env var" not in out
    assert "Removed from User PATH" not in out
    assert fake.deleted == []


def test_uninstall_cli_flag_defaults_to_keeping_the_user_env():
    """``plobi uninstall`` 的默认值必须是「不抹」，只能由命令行显式置真。

    裁定 75 禁止默认开、也禁止做成配置项默认 true，所以默认值本身要有测试守着，而不是
    靠读代码确认。``--full`` 那一行是同一条契约的另一半：删数据不等于抹用户环境。
    """
    import argparse

    from plobi_cli.subcommands.uninstall import build_uninstall_parser

    parser = argparse.ArgumentParser(prog="plobi")
    sub = parser.add_subparsers(dest="command")
    build_uninstall_parser(sub, cmd_uninstall=lambda args: args)

    assert parser.parse_args(["uninstall"]).purge_user_env is False
    assert parser.parse_args(["uninstall", "--yes", "--full"]).purge_user_env is False
    assert parser.parse_args(["uninstall", "--purge-user-env"]).purge_user_env is True

