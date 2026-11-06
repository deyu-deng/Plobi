"""记忆 OAuth 的 profile 作用域：家没了的行不再回 404（裁 178/181 之后按共享家）。

事故（2026-10-26 现读）：桌面把「当前那一行的 profile 名」作为 ``profile=`` 带上
只读的状态查询，而 24 行的家已被搬走 ⇒ 每次都 404 ``Profile 'aura' does not exist.``，
弹层一直在加载态上悬着（27 条 / 一次排查）。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import plobi_constants as pc
from plobi_cli import memory_oauth
from plobi_cli import agent_identity as profiles_mod


def test_missing_home_no_longer_404s(monkeypatch):
    monkeypatch.setattr(profiles_mod, "profile_exists", lambda name: False)
    calls = []
    monkeypatch.setattr(pc, "set_plobi_home_override", lambda path: calls.append(path) or "tok")
    monkeypatch.setattr(pc, "reset_plobi_home_override", lambda tok: None)

    with memory_oauth._scope_to_profile("aura"):
        pass

    assert calls == [], "家不在就不该 override——按共享家继续（裁 178 的终态）"


def test_existing_home_still_overrides(monkeypatch):
    monkeypatch.setattr(profiles_mod, "profile_exists", lambda name: True)
    monkeypatch.setattr(profiles_mod, "get_profile_dir", lambda name: "D:/fake/aura")
    calls, resets = [], []
    monkeypatch.setattr(pc, "set_plobi_home_override", lambda path: calls.append(path) or "tok")
    monkeypatch.setattr(pc, "reset_plobi_home_override", lambda tok: resets.append(tok))

    with memory_oauth._scope_to_profile("aura"):
        pass

    assert calls == ["D:/fake/aura"]
    assert resets == ["tok"], "出作用域必须还原，别把 override 漏给下一个请求"


@pytest.mark.parametrize("name", [None, "", "   ", "current"])
def test_untouched_names_are_noops(monkeypatch, name):
    calls = []
    monkeypatch.setattr(pc, "set_plobi_home_override", lambda path: calls.append(path) or "tok")
    with memory_oauth._scope_to_profile(name):
        pass
    assert calls == []


def test_dirty_name_still_rejected(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        with memory_oauth._scope_to_profile("../escape"):
            pass
    assert exc.value.status_code == 400, "脏输入不是'过时'，仍然要拒"
