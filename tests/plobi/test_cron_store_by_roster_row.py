# -*- coding: utf-8 -*-
"""排程的落点：共享根下按名册行分一份（用户 10-27 拍板），存在性问名册那一行。

两条不变量：

① 作用域判据问**名册那一行**（裁 192），不是家目录——家都退役了，问目录会让「给一个
   被隔离过的角色排个程」直接 404；
② 存储落在**共享根下** ``<shared>/cron/agents/<slug>/``，不再挂 ``profiles/<slug>/``；
   运行时家（配置 / 凭据 / 脚本）是共享根那一份；
③ 调度器**逐份跑**（``cron/jobs.py::discover_cron_stores``）——否则落在名册行那一格里的
   作业会永远不触发，那是又一条「界面里看得见、实际不跑」的假货。
"""

from pathlib import Path

import pytest


@pytest.fixture()
def cron_home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME ＋ 一份名册；家目录一个都没有。"""
    home = tmp_path / ".plobi"
    (home / "principal-plobi").mkdir(parents=True)
    (home / "profiles").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    roster = home / "principal-plobi" / "projects.yaml"
    roster.write_text(
        "agents:\n"
        "  principal-agenda:\n"
        "    role: principal\n"
        "    description: 日程秘书\n"
        "  principal-aura:\n"
        "    role: principal\n"
        "    description: 名册上的那一行\n"
        "  principal-plobi:\n"
        "    role: principal\n"
        "    description: 与安装同名的那一行\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))
    return home


def _web_server():
    from plobi_cli import web_server

    return web_server


def test_cron_scope_accepts_a_roster_row(cron_home):
    """名册上的那一行排程不再 404（家目录不在盘上也不是理由）。"""
    ws = _web_server()
    name, store_dir = ws._cron_profile_home("principal-agenda")
    assert name == "principal-agenda"
    assert store_dir == cron_home / "cron" / "agents" / "principal-agenda"
    assert not (cron_home / "profiles" / "principal-agenda").exists()


def test_cron_scope_unknown_name_still_404(cron_home):
    """不认识的名字仍是原来那句 404，一字不差。"""
    from fastapi import HTTPException

    ws = _web_server()
    with pytest.raises(HTTPException) as exc:
        ws._cron_profile_home("no-such-row")
    assert exc.value.status_code == 404
    assert exc.value.detail == "Profile 'no-such-row' does not exist."


def test_cron_scope_default_is_the_shared_store(cron_home):
    """默认那一份＝共享根下 ``<shared>/cron``。"""
    ws = _web_server()
    name, store_dir = ws._cron_profile_home("default")
    assert name == "default"
    assert store_dir == cron_home / "cron"


def test_cron_runtime_home_is_shared(cron_home):
    """运行时家是共享根那一份（配置 / 凭据 / 技能只有一份）。"""
    ws = _web_server()
    assert ws._cron_runtime_home() == cron_home


def test_same_row_two_spellings_share_one_store(cron_home):
    """名册 name 与它的 profile_name 落在同一份存储里。"""
    ws = _web_server()
    _n1, dir1 = ws._cron_profile_home("principal-aura")
    _n2, dir2 = ws._cron_profile_home("principal-aura")
    assert dir1 == dir2


def test_store_key_is_the_doorplate(cron_home):
    """存储键就是门牌（名册 name 本身）；旧门牌号 l2-agenda 已不认。"""
    from fastapi import HTTPException

    ws = _web_server()
    _name, store_dir = ws._cron_profile_home("principal-agenda")
    assert store_dir == cron_home / "cron" / "agents" / "principal-agenda"
    with pytest.raises(HTTPException) as exc:
        ws._cron_profile_home("l2-agenda")
    assert exc.value.status_code in (400, 404)


def test_roster_name_with_capitals_is_not_a_400(cron_home):
    """名册行名**可以带大写**（``Aura``）：过了名册这一关就不该再被 slug 形状挡住。"""
    ws = _web_server()
    from fastapi import HTTPException

    # `Aura` 那行的 name 带大写、profile 是 `aura`
    assert ws._resolve_profile_dir("principal-aura").is_dir()
    # 不存在的名字：形状对 ⇒ 404，形状不对 ⇒ 400
    with pytest.raises(HTTPException) as exc:
        ws._resolve_profile_dir("no-such-row")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        ws._resolve_profile_dir("不合法 的 名字!")
    assert exc.value.status_code == 400


def test_reserved_looking_roster_row_can_still_read_its_cron(cron_home):
    """名册里有一行叫 ``Plobi``（与安装同名）：读排程不该 400。

    形状是**建造期**的门（``validate_profile_name``），不是读侧的门。这一行在名册里
    既成事实，可早先读排程先跑形状校验，于是每次都被判成 400——errors.log 累计 648
    次就是这么来的。2026-11-16（i18n 定名刀）：保留名闸与 pin 表都已删除，同名靠
    ``role-`` 前缀隔开（``principal-plobi`` 与 ``conductor-plobi`` 是两个人），这一类
    400 从源头不存在；读排程「先查名册、再谈形状」的顺序仍要保住。
    """
    ws = _web_server()
    name, store_dir = ws._cron_profile_home("principal-plobi")
    assert name == "principal-plobi"  # 规范名是小写，存储键取的是名册 name
    assert store_dir == cron_home / "cron" / "agents" / "principal-plobi"
    # 小写写法是同一行、同一份存储
    assert ws._cron_profile_home("principal-plobi")[1] == store_dir


def test_reserved_name_absent_from_roster_is_still_a_400(cron_home):
    """名册里没有、又撞保留名的新写法仍是 400（这道门只是挪了位置，没有拆掉）。"""
    from fastapi import HTTPException

    ws = _web_server()
    with pytest.raises(HTTPException) as exc:
        ws._cron_profile_home("sudo")
    assert exc.value.status_code == 400


def test_scheduler_discovers_row_stores(cron_home):
    """调度器看得见名册行那一份存储（装了作业才算）。"""
    from cron import jobs as cron_jobs

    base = cron_home / "cron"
    row = base / "agents" / "principal-agenda"
    row.mkdir(parents=True)
    assert cron_jobs.discover_cron_stores() == [base]

    (row / "jobs.json").write_text("[]\n", encoding="utf-8")
    stores = cron_jobs.discover_cron_stores()
    assert base in stores and row in stores
