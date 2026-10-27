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
    (home / "plobi").mkdir(parents=True)
    (home / "profiles").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    roster = home / "plobi" / "projects.yaml"
    roster.write_text(
        "agents:\n"
        "  agenda:\n"
        "    role: l2_agenda\n"
        "    description: 日程秘书\n"
        "  Aura:\n"
        "    role: l2_project\n"
        "    description: 名册上的那一行\n",
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
    name, store_dir = ws._cron_profile_home("agenda")
    assert name == "agenda"
    assert store_dir == cron_home / "cron" / "agents" / "agenda"
    assert not (cron_home / "profiles" / "agenda").exists()


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
    _n1, dir1 = ws._cron_profile_home("Aura")
    _n2, dir2 = ws._cron_profile_home("aura")
    assert dir1 == dir2


def test_scheduler_discovers_row_stores(cron_home):
    """调度器看得见名册行那一份存储（装了作业才算）。"""
    from cron import jobs as cron_jobs

    base = cron_home / "cron"
    row = base / "agents" / "agenda"
    row.mkdir(parents=True)
    assert cron_jobs.discover_cron_stores() == [base]

    (row / "jobs.json").write_text("[]\n", encoding="utf-8")
    stores = cron_jobs.discover_cron_stores()
    assert base in stores and row in stores
