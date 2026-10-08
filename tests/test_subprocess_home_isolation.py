"""Tests for subprocess HOME handling in profile mode.

Plobi state stays profile-scoped through PLOBI_HOME. Host subprocesses should
keep the user's real HOME by default so external CLIs find existing credentials.
Containers still use the profile home for persistence, and users can explicitly
opt into profile HOME isolation on the host.

See: https://github.com/NousResearch/hermes-agent/issues/25114
See: https://github.com/NousResearch/hermes-agent/issues/36144
See: https://github.com/NousResearch/hermes-agent/issues/29015
"""

import os
import threading
from pathlib import Path

import pytest

import plobi_constants


def _is_volume_root(path: str) -> bool:
    """True for ``/`` or a drive root like ``D:\\`` — where abspath stops moving."""
    absolute = os.path.abspath(path)
    return absolute == os.path.dirname(absolute)


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))



# ---------------------------------------------------------------------------
# get_subprocess_home()
# ---------------------------------------------------------------------------

class TestGetSubprocessHome:
    """Unit tests for plobi_constants.get_subprocess_home()."""

    def _host_mode(self, monkeypatch):
        monkeypatch.setattr(plobi_constants, "is_container", lambda: False)
        monkeypatch.delenv("TERMINAL_HOME_MODE", raising=False)
        monkeypatch.delenv("PLOBI_REAL_HOME", raising=False)

    def _container_mode(self, monkeypatch):
        monkeypatch.setattr(plobi_constants, "is_container", lambda: True)
        monkeypatch.delenv("TERMINAL_HOME_MODE", raising=False)
        monkeypatch.delenv("PLOBI_REAL_HOME", raising=False)

    def test_returns_none_when_plobi_home_unset(self, monkeypatch):
        monkeypatch.delenv("PLOBI_HOME", raising=False)
        from plobi_constants import get_subprocess_home
        assert get_subprocess_home() is None

    def test_returns_none_when_home_dir_missing(self, tmp_path, monkeypatch):
        plobi_home = tmp_path / ".plobi"
        plobi_home.mkdir()
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
        # No home/ subdirectory created
        from plobi_constants import get_subprocess_home
        assert get_subprocess_home() is None

    def test_host_auto_keeps_real_home_when_profile_home_exists(self, tmp_path, monkeypatch):
        """Host installs should not hide real ~/.ssh, ~/.gitconfig, ~/.azure, etc."""
        self._host_mode(monkeypatch)
        real_home = tmp_path / "real-home"
        plobi_home = real_home / ".plobi" / "profiles" / "coder"
        profile_home = plobi_home / "home"
        profile_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(real_home))
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
        from plobi_constants import get_subprocess_home
        assert get_subprocess_home() is None

    def test_container_auto_uses_profile_home_when_home_dir_exists(self, tmp_path, monkeypatch):
        self._container_mode(monkeypatch)
        plobi_home = tmp_path / ".plobi"
        profile_home = plobi_home / "home"
        profile_home.mkdir(parents=True)
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
        from plobi_constants import get_subprocess_home
        assert get_subprocess_home() == str(profile_home)

    def test_returns_profile_specific_path(self, tmp_path, monkeypatch):
        """Explicit profile mode keeps the old per-profile HOME behavior."""
        self._host_mode(monkeypatch)
        profile_dir = tmp_path / ".plobi" / "profiles" / "coder"
        profile_dir.mkdir(parents=True)
        profile_home = profile_dir / "home"
        profile_home.mkdir()
        monkeypatch.setenv("TERMINAL_HOME_MODE", "profile")
        monkeypatch.setenv("PLOBI_HOME", str(profile_dir))
        from plobi_constants import get_subprocess_home
        assert get_subprocess_home() == str(profile_home)

    def test_real_mode_repairs_parent_home_already_pointing_at_profile(self, tmp_path, monkeypatch):
        self._host_mode(monkeypatch)
        profile_dir = tmp_path / ".plobi" / "profiles" / "coder"
        profile_home = profile_dir / "home"
        profile_home.mkdir(parents=True)
        real_home = tmp_path / "real-home"
        real_home.mkdir()
        monkeypatch.setenv("TERMINAL_HOME_MODE", "real")
        monkeypatch.setenv("PLOBI_HOME", str(profile_dir))
        monkeypatch.setenv("HOME", str(profile_home))
        monkeypatch.setenv("PLOBI_REAL_HOME", str(real_home))

        from plobi_constants import get_subprocess_home, get_real_home

        assert get_real_home() == str(real_home)
        assert get_subprocess_home() == str(real_home)

    def test_real_home_falls_back_to_os_account_when_home_is_profile(self, tmp_path, monkeypatch):
        self._host_mode(monkeypatch)
        profile_dir = tmp_path / ".plobi" / "profiles" / "coder"
        profile_home = profile_dir / "home"
        profile_home.mkdir(parents=True)
        monkeypatch.setenv("PLOBI_HOME", str(profile_dir))
        monkeypatch.setenv("HOME", str(profile_home))

        from plobi_constants import get_real_home

        assert get_real_home() != str(profile_home)

    def test_two_profiles_get_different_homes(self, tmp_path, monkeypatch):
        self._container_mode(monkeypatch)
        base = tmp_path / ".plobi" / "profiles"
        for name in ("alpha", "beta"):
            p = base / name
            p.mkdir(parents=True)
            (p / "home").mkdir()

        from plobi_constants import get_subprocess_home

        monkeypatch.setenv("PLOBI_HOME", str(base / "alpha"))
        home_a = get_subprocess_home()

        monkeypatch.setenv("PLOBI_HOME", str(base / "beta"))
        home_b = get_subprocess_home()

        assert home_a is not None
        assert home_b is not None
        assert home_a != home_b
        assert home_a.endswith("alpha/home")
        assert home_b.endswith("beta/home")

    def test_context_override_is_thread_local(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        profile = tmp_path / "profile"
        root.mkdir()
        profile.mkdir()
        monkeypatch.setenv("PLOBI_HOME", str(root))

        from plobi_constants import (
            get_plobi_home,
            reset_plobi_home_override,
            set_plobi_home_override,
        )

        ready = threading.Event()
        release = threading.Event()
        seen: list[str] = []

        def read_from_other_thread():
            ready.set()
            release.wait(timeout=5)
            seen.append(str(get_plobi_home()))

        thread = threading.Thread(target=read_from_other_thread)
        thread.start()
        assert ready.wait(timeout=5)

        token = set_plobi_home_override(profile)
        try:
            assert get_plobi_home() == profile
            release.set()
            thread.join(timeout=5)
        finally:
            reset_plobi_home_override(token)
            release.set()

        assert seen == [str(root)]
        assert get_plobi_home() == root


# ---------------------------------------------------------------------------
# get_temp_root() — the one temp root shared by tests and the runtime
# ---------------------------------------------------------------------------

class TestTempRoot:
    """"/tmp" is a POSIX literal, not a fallback.

    Native Python resolves it against the *current drive*, so on a machine whose
    cwd is on ``D:\\`` the last-resort home became ``D:\\tmp`` and a live gateway
    wrote its runtime state there (WP-D-ROOT-RESIDUE, 裁定 79). The invariant is
    pinned here, not a list of forbidden directory names: whatever comes back has
    to be an existing, writable directory that is not a volume root, and it has to
    be derived from the environment so a machine can point it at its own policy
    temp area (no drive letter in the code, 裁定 48.1).
    """

    def _strip_temp_env(self, monkeypatch):
        for var in ("TMPDIR", "TEMP", "TMP"):
            monkeypatch.delenv(var, raising=False)

    def test_env_var_is_honoured_rather_than_a_literal(self, tmp_path, monkeypatch):
        """A machine's policy temp area wins, whatever the OS default is."""
        policy_root = tmp_path / "Temp"
        policy_root.mkdir()
        self._strip_temp_env(monkeypatch)
        monkeypatch.setenv("TMPDIR", str(policy_root))
        assert _same_path(plobi_constants.get_temp_root(), str(policy_root))

    def test_precedence_follows_the_platform_temp_order(self, tmp_path, monkeypatch):
        """TMPDIR > TEMP > TMP, the same order tempfile.gettempdir() uses."""
        roots = {}
        for name in ("first", "second", "third"):
            roots[name] = tmp_path / name
            roots[name].mkdir()
        self._strip_temp_env(monkeypatch)
        monkeypatch.setenv("TMPDIR", str(roots["first"]))
        monkeypatch.setenv("TEMP", str(roots["second"]))
        monkeypatch.setenv("TMP", str(roots["third"]))
        assert _same_path(plobi_constants.get_temp_root(), str(roots["first"]))
        monkeypatch.delenv("TMPDIR")
        assert _same_path(plobi_constants.get_temp_root(), str(roots["second"]))
        monkeypatch.delenv("TEMP")
        assert _same_path(plobi_constants.get_temp_root(), str(roots["third"]))

    def test_missing_dir_and_plain_file_are_skipped(self, tmp_path, monkeypatch):
        missing = tmp_path / "does-not-exist"
        a_file = tmp_path / "not-a-dir"
        a_file.write_text("x")
        usable = tmp_path / "usable"
        usable.mkdir()
        self._strip_temp_env(monkeypatch)
        monkeypatch.setenv("TMPDIR", str(missing))
        monkeypatch.setenv("TEMP", str(a_file))
        monkeypatch.setenv("TMP", str(usable))
        assert _same_path(plobi_constants.get_temp_root(), str(usable))

    def test_nothing_set_yields_an_existing_non_root_directory(self, monkeypatch):
        self._strip_temp_env(monkeypatch)
        root = plobi_constants.get_temp_root()
        assert os.path.isabs(root)
        assert os.path.isdir(root), f"{root} is not a directory we can write into"
        assert not _is_volume_root(root)

    def test_resolving_creates_nothing(self, tmp_path, monkeypatch):
        """Import-time / pre-run helper: it must not mkdir on the way by."""
        would_be = tmp_path / "must-not-be-created"
        self._strip_temp_env(monkeypatch)
        monkeypatch.setenv("TMPDIR", str(would_be))
        plobi_constants.get_temp_root()
        assert not would_be.exists()

    def test_posix_literal_is_never_handed_back_on_windows(self, monkeypatch):
        """The regression itself: TEMP=/tmp (what Git Bash sets) must not win."""
        if os.name != "nt":
            pytest.skip("only Windows resolves a leading '/' against the current drive")
        self._strip_temp_env(monkeypatch)
        monkeypatch.setenv("TMPDIR", "/tmp")
        monkeypatch.setenv("TEMP", "/tmp")
        root = plobi_constants.get_temp_root()
        assert root != "/tmp"
        assert os.path.splitdrive(os.path.abspath(root))[0], f"{root} is drive-relative"
        assert os.path.isdir(root)
        assert not _is_volume_root(root)

    def test_posix_keeps_the_old_tmp_semantics(self, monkeypatch):
        """POSIX behaviour is unchanged: TMPDIR=/tmp is a real directory there."""
        if os.name == "nt":
            pytest.skip("/tmp is not a directory on Windows")
        if not os.path.isdir("/tmp"):
            pytest.skip("no /tmp on this POSIX host")
        self._strip_temp_env(monkeypatch)
        monkeypatch.setenv("TMPDIR", "/tmp")
        assert plobi_constants.get_temp_root() == "/tmp"


# ---------------------------------------------------------------------------
# get_real_home() last resort
# ---------------------------------------------------------------------------

class TestRealHomeLastResort:
    """A process with no resolvable account home must not invent a drive-root path.

    This is the runtime half of the bug: a service started without HOME /
    USERPROFILE fell through every candidate and got the literal that became
    ``D:\\tmp``, and the live gateway kept writing ``active_sessions.json`` there.
    """

    def _no_candidates(self, monkeypatch):
        monkeypatch.delenv("PLOBI_REAL_HOME", raising=False)
        monkeypatch.setattr(plobi_constants, "_iter_real_home_candidates", lambda env=None: [])

    def test_last_resort_is_the_shared_temp_root(self, monkeypatch):
        self._no_candidates(monkeypatch)
        assert plobi_constants.get_real_home() == plobi_constants.get_temp_root()

    def test_last_resort_is_a_real_directory_and_not_a_volume_root(self, monkeypatch):
        self._no_candidates(monkeypatch)
        home = plobi_constants.get_real_home()
        assert os.path.isabs(home)
        assert os.path.isdir(home)
        assert not _is_volume_root(home)

    def test_last_resort_is_not_a_drive_relative_literal_on_windows(self, monkeypatch):
        if os.name != "nt":
            pytest.skip("POSIX keeps /tmp as its temp root")
        self._no_candidates(monkeypatch)
        home = plobi_constants.get_real_home()
        assert not home.startswith("/"), f"{home} would resolve against the current drive"
        assert os.path.splitdrive(home)[0]

    def test_subprocess_home_never_becomes_the_tmp_literal(self, tmp_path, monkeypatch):
        """Auto mode, HOME already at the profile home, no real home to repair to."""
        monkeypatch.setattr(plobi_constants, "is_container", lambda: False)
        monkeypatch.delenv("TERMINAL_HOME_MODE", raising=False)
        monkeypatch.delenv("PLOBI_REAL_HOME", raising=False)
        profile_dir = tmp_path / ".plobi" / "profiles" / "coder"
        profile_home = profile_dir / "home"
        profile_home.mkdir(parents=True)
        monkeypatch.setenv("PLOBI_HOME", str(profile_dir))
        monkeypatch.setenv("HOME", str(profile_home))
        monkeypatch.setattr(plobi_constants, "_iter_real_home_candidates", lambda env=None: [])

        home = plobi_constants.get_subprocess_home()

        assert home is not None
        assert os.path.abspath(home) != os.path.abspath(str(profile_home))
        assert os.path.isdir(home)
        assert not _is_volume_root(home)
        if os.name == "nt":
            assert home != "/tmp"
            assert os.path.splitdrive(home)[0]


# ---------------------------------------------------------------------------
# _make_run_env() injection
# ---------------------------------------------------------------------------

class TestMakeRunEnvHomeInjection:
    """Verify _make_run_env() applies the subprocess HOME policy."""

    def test_host_auto_preserves_real_home_when_profile_home_exists(self, tmp_path, monkeypatch):
        plobi_home = tmp_path / "plobi"
        plobi_home.mkdir()
        (plobi_home / "home").mkdir()
        real_home = tmp_path / "real-home"
        real_home.mkdir()
        monkeypatch.setattr(plobi_constants, "is_container", lambda: False)
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
        monkeypatch.setenv("HOME", str(real_home))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from tools.environments.local import _make_run_env
        result = _make_run_env({})

        assert result["HOME"] == str(real_home)
        assert result["PLOBI_REAL_HOME"] == str(real_home)

    def test_profile_mode_injects_profile_home_when_profile_home_exists(self, tmp_path, monkeypatch):
        plobi_home = tmp_path / "plobi"
        plobi_home.mkdir()
        (plobi_home / "home").mkdir()
        real_home = tmp_path / "real-home"
        real_home.mkdir()
        monkeypatch.setattr(plobi_constants, "is_container", lambda: False)
        monkeypatch.setenv("TERMINAL_HOME_MODE", "profile")
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
        monkeypatch.setenv("HOME", str(real_home))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from tools.environments.local import _make_run_env
        result = _make_run_env({})

        assert result["HOME"] == str(plobi_home / "home")
        assert result["PLOBI_REAL_HOME"] == str(real_home)

    def test_no_injection_when_home_dir_missing(self, tmp_path, monkeypatch):
        plobi_home = tmp_path / "plobi"
        plobi_home.mkdir()
        # No home/ subdirectory
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
        monkeypatch.setenv("HOME", "/root")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from tools.environments.local import _make_run_env
        result = _make_run_env({})

        assert result["HOME"] == "/root"

    def test_no_injection_when_plobi_home_unset(self, monkeypatch):
        monkeypatch.delenv("PLOBI_HOME", raising=False)
        monkeypatch.setenv("HOME", "/home/user")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from tools.environments.local import _make_run_env
        result = _make_run_env({})

        assert result["HOME"] == "/home/user"

    def test_context_override_bridges_to_subprocess_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plobi_constants, "is_container", lambda: True)
        root = tmp_path / "root"
        profile = tmp_path / "profile"
        root.mkdir()
        profile.mkdir()
        (profile / "home").mkdir()
        monkeypatch.setenv("PLOBI_HOME", str(root))
        monkeypatch.setenv("HOME", "/root")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from plobi_constants import reset_plobi_home_override, set_plobi_home_override
        from tools.environments.local import _make_run_env

        token = set_plobi_home_override(profile)
        try:
            result = _make_run_env({})
        finally:
            reset_plobi_home_override(token)

        assert result["PLOBI_HOME"] == str(profile)
        assert result["HOME"] == str(profile / "home")


# ---------------------------------------------------------------------------
# _sanitize_subprocess_env() injection
# ---------------------------------------------------------------------------

class TestSanitizeSubprocessEnvHomeInjection:
    """Verify _sanitize_subprocess_env() applies the subprocess HOME policy."""

    def test_host_auto_preserves_real_home_when_profile_home_exists(self, tmp_path, monkeypatch):
        plobi_home = tmp_path / "plobi"
        plobi_home.mkdir()
        (plobi_home / "home").mkdir()
        real_home = tmp_path / "real-home"
        real_home.mkdir()
        monkeypatch.setattr(plobi_constants, "is_container", lambda: False)
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))

        base_env = {"HOME": str(real_home), "PATH": "/usr/bin", "USER": "root"}
        from tools.environments.local import _sanitize_subprocess_env
        result = _sanitize_subprocess_env(base_env)

        assert result["HOME"] == str(real_home)
        assert result["PLOBI_REAL_HOME"] == str(real_home)

    def test_profile_mode_injects_profile_home_when_profile_home_exists(self, tmp_path, monkeypatch):
        plobi_home = tmp_path / "plobi"
        plobi_home.mkdir()
        (plobi_home / "home").mkdir()
        real_home = tmp_path / "real-home"
        real_home.mkdir()
        monkeypatch.setattr(plobi_constants, "is_container", lambda: False)
        monkeypatch.setenv("TERMINAL_HOME_MODE", "profile")
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))

        base_env = {"HOME": str(real_home), "PATH": "/usr/bin", "USER": "root"}
        from tools.environments.local import _sanitize_subprocess_env
        result = _sanitize_subprocess_env(base_env)

        assert result["HOME"] == str(plobi_home / "home")
        assert result["PLOBI_REAL_HOME"] == str(real_home)

    def test_no_injection_when_home_dir_missing(self, tmp_path, monkeypatch):
        plobi_home = tmp_path / "plobi"
        plobi_home.mkdir()
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))

        base_env = {"HOME": "/root", "PATH": "/usr/bin"}
        from tools.environments.local import _sanitize_subprocess_env
        result = _sanitize_subprocess_env(base_env)

        assert result["HOME"] == "/root"

    def test_context_override_bridges_to_background_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plobi_constants, "is_container", lambda: True)
        root = tmp_path / "root"
        profile = tmp_path / "profile"
        root.mkdir()
        profile.mkdir()
        (profile / "home").mkdir()
        monkeypatch.setenv("PLOBI_HOME", str(root))

        base_env = {"HOME": "/root", "PATH": "/usr/bin"}
        from plobi_constants import reset_plobi_home_override, set_plobi_home_override
        from tools.environments.local import _sanitize_subprocess_env

        token = set_plobi_home_override(profile)
        try:
            result = _sanitize_subprocess_env(base_env)
        finally:
            reset_plobi_home_override(token)

        assert result["PLOBI_HOME"] == str(profile)
        assert result["HOME"] == str(profile / "home")


# ---------------------------------------------------------------------------
# Profile bootstrap
# ---------------------------------------------------------------------------

class TestProfileBootstrap:
    """Verify new profiles get a home/ subdirectory."""

    def test_profile_dirs_includes_home(self):
        from plobi_cli.profiles import _PROFILE_DIRS
        assert "home" in _PROFILE_DIRS

    def test_create_profile_bootstraps_home_dir(self, tmp_path, monkeypatch):
        """create_profile() should create home/ inside the profile dir."""
        home = tmp_path / ".plobi"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("PLOBI_HOME", str(home))

        from plobi_cli.profiles import create_profile
        profile_dir = create_profile("testbot", no_alias=True)
        assert (profile_dir / "home").is_dir()


# ---------------------------------------------------------------------------
# Python process HOME unchanged
# ---------------------------------------------------------------------------

class TestPythonProcessUnchanged:
    """Confirm the Python process's own HOME is never modified."""

    def test_path_home_unchanged_after_subprocess_home_resolved(
        self, tmp_path, monkeypatch
    ):
        plobi_home = tmp_path / "plobi"
        plobi_home.mkdir()
        (plobi_home / "home").mkdir()
        monkeypatch.setenv("PLOBI_HOME", str(plobi_home))

        original_home = os.environ.get("HOME")
        original_path_home = str(Path.home())

        from plobi_constants import get_subprocess_home
        sub_home = get_subprocess_home()

        # Resolving subprocess HOME must not mutate the Python process env.
        assert sub_home in (None, str(plobi_home / "home"), original_home)
        assert os.environ.get("HOME") == original_home
        assert str(Path.home()) == original_path_home
