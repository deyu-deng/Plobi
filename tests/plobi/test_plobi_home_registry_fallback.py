"""PLOBI_HOME 的第三条来源：Windows 注册表（`install.ps1` 用 `setx` 钉的那份）。

病根（2026-10-22 现核）：`setx` 只对**之后登录**的进程生效。在它之前就起来的
长驻进程——桌面、CodeBuddy、计划任务、旧 shell——env 里根本没有 `PLOBI_HOME`，
于是每次解析都退回 `%LOCALAPPDATA%\\plobi`（C 盘），而 24 个 profile 家全在
D 盘。桌面侧早就读了注册表兜底（`electron/main.ts::resolvePlobiHome()` 第 3 条），
**Python 侧一直没有**，于是同一个家在两种进程里解析到两个地方。

用户可见的后果：`say` 投递成功（消息落库、`ok: true`），但 `woke: false`，
`wakeNote` 说「agenda 还没在这台机器上落地，问不到它——登记 / 新建一次就问到」——
一句**假否定**，家明明在。留言躺在会话里没人看。

这些测试钉住三件事：
  1. env 没有、注册表有 ⇒ 解析到注册表那份（不是 C 盘）；
  2. 两边都没有 ⇒ 行为与改动前逐位相同（native 默认）；
  3. `resolve_profile_env` 因此找得到 profile 家（say/ask 叫醒人的前提）。
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

import pytest

import plobi_constants
from plobi_cli.profiles import resolve_profile_env


@pytest.fixture()
def no_env_home(monkeypatch):
    """把 env 那一项摘掉，只留注册表那一条路。"""
    monkeypatch.delenv("PLOBI_HOME", raising=False)
    # 上下文覆盖是第三优先级，摘掉它才测得到"env 没有"这条真路径。
    monkeypatch.setattr(plobi_constants, "_PLOBI_HOME_OVERRIDE", ContextVar("test_home_override", default=plobi_constants._UNSET))


def test_registry_pin_wins_when_env_is_absent(no_env_home, monkeypatch):
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: r"D:\Data\AppData\plobi")

    assert plobi_constants.get_plobi_home() == Path(r"D:\Data\AppData\plobi")
    assert plobi_constants.get_default_plobi_root() == Path(r"D:\Data\AppData\plobi")


def test_no_env_and_no_registry_falls_back_to_native(no_env_home, monkeypatch):
    """两边都没有 ⇒ 与改动前逐位相同（POSIX 上尤其：那条路本来就不读注册表）。"""
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: None)

    assert plobi_constants.get_plobi_home() == plobi_constants._get_platform_default_plobi_home()
    assert plobi_constants.get_default_plobi_root() == plobi_constants._get_platform_default_plobi_home()


def test_profile_home_resolves_from_the_pin(no_env_home, monkeypatch, tmp_path):
    """`resolve_profile_env` 找得到家——这是 `say` 能叫醒任何人的前提。"""
    root = tmp_path / "plobi-home"
    (root / "profiles" / "l2-agenda").mkdir(parents=True)
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: str(root))

    assert resolve_profile_env("l2-agenda") == str(root / "profiles" / "l2-agenda")


def test_env_still_wins_over_the_registry(monkeypatch):
    """显式 env 是最高优先级（install.ps1 与 spawner 传的那份不许被注册表盖掉）。"""
    monkeypatch.setenv("PLOBI_HOME", r"D:\Env\Wins")
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: r"D:\Registry\Loses")

    assert plobi_constants.get_plobi_home() == Path(r"D:\Env\Wins")
    assert plobi_constants.get_default_plobi_root() == Path(r"D:\Env\Wins")


def test_a_profile_shaped_pin_resolves_to_its_root(no_env_home, monkeypatch, tmp_path):
    """注册表那份是 `<root>/profiles/<name>` 时返回 `<root>`（profile 模式）。"""
    root = tmp_path / "root"
    home = root / "profiles" / "coder"
    home.mkdir(parents=True)
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: str(home))

    assert plobi_constants.get_default_plobi_root() == root
