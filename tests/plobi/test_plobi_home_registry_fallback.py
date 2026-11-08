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
from plobi_cli.agent_identity import resolve_profile_env


@pytest.fixture()
def no_env_home(monkeypatch):
    """把 env 那一项摘掉，只留注册表那一条路。"""
    monkeypatch.delenv("PLOBI_HOME", raising=False)
    # 开发态门牌也要摘：这机器上它是有值的，不摘会让"env 没有"的用例假失败。
    monkeypatch.delenv("PLOBI_DEV_HOME", raising=False)
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


# ── 2026-11-08：装机器只记录 DataDir，不再写 HKCU\Environment ──────────────
#
# WP-DATA-ISOLATION：安装器把 `PLOBI_HOME` / `MIND_ROOT` / `SURFACE_ROOT` 写进
# `HKCU\Environment` 是"两份身份共用一块门牌"的污染源（装机版与开发态谁后装谁
# 把对方带偏）。从这一天起安装记录只剩一格 `HKCU\Software\Plobi\DataDir`，于是
# 干净 shell 里的命令行 `plobi`（Path 指向装机版 bin）必须靠这一格找到家。


def test_installer_recorded_data_dir_wins_over_the_old_env_pin(no_env_home, monkeypatch):
    """安装记录排在"老环境变量残留"之前：记录是本次安装的事实，残留是上一次的门牌。"""
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: r"D:\Old\Plobi")
    monkeypatch.setattr(plobi_constants, "_install_recorded_data_dir", lambda: r"D:\Data\AppData\Plobi")

    assert plobi_constants.get_plobi_home() == Path(r"D:\Data\AppData\Plobi")
    assert plobi_constants.get_default_plobi_root() == Path(r"D:\Data\AppData\Plobi")


def test_explicit_env_still_wins_over_the_installer_record(monkeypatch):
    """显式 PLOBI_HOME 压顶：桌面 spawn 子进程时写的就是这一格（开发态靠它读 Plobi-Dev）。"""
    monkeypatch.setenv("PLOBI_HOME", r"D:\Data\AppData\Plobi-Dev")
    monkeypatch.setattr(plobi_constants, "_install_recorded_data_dir", lambda: r"D:\Data\AppData\Plobi")

    assert plobi_constants.get_plobi_home() == Path(r"D:\Data\AppData\Plobi-Dev")


def test_no_env_no_record_no_registry_returns_native(no_env_home, monkeypatch):
    """三档都没有 ⇒ 逐位回到平台默认（这条保证新档不会偷偷改掉默认行为）。"""
    monkeypatch.setattr(plobi_constants, "_install_recorded_data_dir", lambda: None)
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: None)

    assert plobi_constants.get_plobi_home() == plobi_constants._get_platform_default_plobi_home()


# ── 2026-11-08：开发态改用独立门牌 PLOBI_DEV_HOME ───────────────────────────
#
# 两份身份共用一个 PLOBI_HOME 名字是污染根源。开发态改认 PLOBI_DEV_HOME 之后，
# 下面三条是它的全部约束：成对的身份优先级、以及"装机版门牌串进开发态变量"的自愈。


def test_dev_home_wins_over_the_installer_record(no_env_home, monkeypatch):
    """没人钉 PLOBI_HOME 时（手敲 plobi 的命令行），PLOBI_DEV_HOME 顶在安装记录之前。

    这正是要修的那条：删掉用户级 PLOBI_HOME 后，命令行若退到 DataDir
    就会读写装机版数据目录，两份数据搅在一起。
    """
    monkeypatch.setenv("PLOBI_DEV_HOME", r"D:\Data\AppData\Plobi-Dev")
    monkeypatch.setattr(plobi_constants, "_install_recorded_data_dir", lambda: r"D:\Data\AppData\Plobi")

    assert plobi_constants.get_plobi_home() == Path(r"D:\Data\AppData\Plobi-Dev")
    assert plobi_constants.get_default_plobi_root() == Path(r"D:\Data\AppData\Plobi-Dev")


def test_pinned_plobi_home_beats_dev_home(no_env_home, monkeypatch):
    """隔离硬约束：装机版后端被父进程钉了 PLOBI_HOME 时，用户级 PLOBI_DEV_HOME 不许插队。

    装机版桌面 spawn 后端会把 PLOBI_HOME 写进子进程 env，而子进程照样继承用户级
    PLOBI_DEV_HOME。这条钉住"装机版永远读不到开发态目录"。
    """
    monkeypatch.setenv("PLOBI_HOME", r"D:\Data\AppData\Plobi")
    monkeypatch.setenv("PLOBI_DEV_HOME", r"D:\Data\AppData\Plobi-Dev")
    monkeypatch.setattr(plobi_constants, "_install_recorded_data_dir", lambda: r"D:\Data\AppData\Plobi")

    assert plobi_constants.get_plobi_home() == Path(r"D:\Data\AppData\Plobi")


def test_dev_home_equal_to_installer_record_is_ignored(no_env_home, monkeypatch):
    """自愈：PLOBI_DEV_HOME 被写成了装机版目录（同址）⇒ 判污染忽略，退到安装记录。

    与 `electron/data-root.ts` 2.1 的判定同一条规则：同一个值出现在两个身份的
    门牌上时，说明有人写串了，宁可走安装记录也不要把开发态拽进装机版数据目录。
    """
    monkeypatch.setenv("PLOBI_DEV_HOME", r"D:\Data\AppData\Plobi")
    monkeypatch.setattr(plobi_constants, "_install_recorded_data_dir", lambda: r"D:\Data\AppData\Plobi")
    monkeypatch.setattr(plobi_constants, "_pinned_home_from_registry", lambda: None)

    assert plobi_constants.get_plobi_home() == Path(r"D:\Data\AppData\Plobi")
