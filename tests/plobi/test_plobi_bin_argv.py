"""11-23 假唤醒的根：桌面端里 `sys.executable` 是 Electron 壳，不是解释器。

`module_plobi_argv()` 当年无条件返回 `[sys.executable, "-m", "plobi_cli.main"]`，
`-m` 没有任何解释器去看，孩子起来就是壳自己，几秒后带着 GPU cache 报错死掉；
`wake_agent` 只看 `Popen` 返不返回，于是 `woke=true` 一路写进证据。

这里钉三件事：
1. 宿主不是解释器时，必须改认安装树 venv 里的命令行；
2. 显式 `PLOBI_PYTHON` / `PLOBI_PY` 优先于猜出来的路径；
3. 实在没有能跑的，`plobi_argv_runnable` 必须说实话（False）——让唤醒链
   报失败，而不是再假成功一遍。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "plobi_cli" / "plobi_bin.py"


@pytest.fixture(scope="module")
def PB():
    name = "plobi_test_bin_module"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture()
def gui_host(monkeypatch, tmp_path):
    """把当前进程装成桌面端：`sys.executable` 指向 GUI 壳，且被标记 frozen。"""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Plobi.exe"), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    for env_name in ("PLOBI_PYTHON", "PLOBI_PY", "PLOBI_HOME"):
        monkeypatch.delenv(env_name, raising=False)


def _install_venv(home: Path) -> Path:
    scripts = home / "plobi-agent" / "venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    exe = scripts / "plobi.exe"
    exe.write_text("", encoding="utf-8")
    return exe


def test_a_python_host_keeps_the_interpreter_bound_invocation(PB, monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Python311\python.exe", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert PB._host_is_python_interpreter() is True
    assert PB.module_plobi_argv() == [r"C:\Python311\python.exe", "-m", "plobi_cli.main"]


def test_a_gui_host_falls_back_to_the_installed_venv(PB, gui_host, monkeypatch, tmp_path):
    exe = _install_venv(tmp_path / "home")
    monkeypatch.setattr(PB, "_candidate_plobi_homes", lambda: [tmp_path / "home"])

    assert PB._host_is_python_interpreter() is False
    assert PB.module_plobi_argv() == [str(exe)]
    assert PB.plobi_argv_runnable([str(exe)]) is True


def test_an_explicit_interpreter_wins_over_the_venv(PB, gui_host, monkeypatch, tmp_path):
    """运维手上那条 `PLOBI_PYTHON` 优先——它比猜出来的安装树更可信。"""
    _install_venv(tmp_path / "home")
    explicit = tmp_path / "py" / "python.exe"
    explicit.parent.mkdir(parents=True, exist_ok=True)
    explicit.write_text("", encoding="utf-8")
    monkeypatch.setenv("PLOBI_PYTHON", str(explicit))
    monkeypatch.setattr(PB, "_candidate_plobi_homes", lambda: [tmp_path / "home"])

    assert PB.module_plobi_argv() == [str(explicit), "-m", "plobi_cli.main"]


def test_a_gui_host_with_nothing_runnable_is_reported_as_not_runnable(
    PB, gui_host, monkeypatch
):
    """没有 venv、没有显式指定：退回宿主 exe，但判定必须说「跑不起来」。

    退回本身是为了让调用方拿到 argv 去报错，不是为了让它继续假成功。
    """
    monkeypatch.setattr(PB, "_candidate_plobi_homes", lambda: [])

    argv = PB.module_plobi_argv()

    assert argv == [sys.executable, "-m", "plobi_cli.main"]
    assert PB.plobi_argv_runnable(argv) is False


def test_the_install_that_runs_it_wins_over_the_env_home(PB, gui_host, monkeypatch, tmp_path):
    """自身位置优先：11-23 现场 `Plobi` 与 `Plobi-Dev` 两套同时在跑，
    环境变量会指到隔壁，只有「我被谁装进去」不会撒谎。"""
    home = tmp_path / "Plobi"
    exe = _install_venv(home)
    module_file = (
        home / "plobi-agent" / "venv" / "Lib" / "site-packages" / "plobi_cli" / "plobi_bin.py"
    )
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(PB, "__file__", str(module_file), raising=False)
    monkeypatch.setenv("PLOBI_DEV_HOME", str(tmp_path / "Plobi-Dev"))

    assert PB._home_from_this_module() == home
    assert PB.module_plobi_argv() == [str(exe)]


def test_the_frozen_flag_alone_disqualifies_the_host(PB, monkeypatch, tmp_path):
    """名字叫 `python.exe` 也不认——PyInstaller 打包后会捏成这种形状。"""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert PB._host_is_python_interpreter() is False
