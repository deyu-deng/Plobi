"""档案本体的**字段级**编辑手（``dossier_write``）——WP-DOSSIER-SHAPE。

钉住五件事：**只改点名的几格**（文件里我们不认识的键原样留着）、**``updated`` 由代码盖**
（模型不填日期）、**坏形状不写盘**（一个字节都不许动）、**只认档案本体**（别的路径当场拒）、
**写完能读回来**（原子落盘，目录里不留半份残骸）。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from plobi.mind.provider import MindProvider

PROFILE: dict = {
    "version": 1,
    "agent": "Aura",
    "title": "Aura",
    "category": "projects",
    "goal": "旧目标",
    "status": "旧现状",
    "stages": [{"id": "s1", "title": "第一阶段", "detail": "交付标准", "done": False}],
    "registry": {"state": "active"},
    "updated": "2026-09-28",
    "log": "# Aura · 流水",
    # 别人的键：这一层不认它，但它不许被我们的写回洗掉（只增不改）。
    "别人的键": {"留着": True},
}

AURA = "Dossiers/projects/Aura/dossier.json"
SRTP = "Dossiers/research/SRTP/dossier.json"


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki" / "daily").mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    (surface / "dossiers" / "research" / "SRTP").mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Aura" / "dossier.json").write_text(
        json.dumps(PROFILE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (surface / "dossiers" / "research" / "SRTP" / "dossier.json").write_text(
        json.dumps(
            {"agent": "SRTP", "goal": "研究目标", "status": "研究现状"}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    provider = MindProvider()
    provider.initialize("sess-dossier", agent_context="primary")
    return mind, surface, provider


def _call(provider, **args):
    return json.loads(provider.handle_tool_call("dossier_write", args))


def test_goal_and_status_change_and_unknown_keys_survive(roots):
    _mind, surface, provider = roots
    path = surface / "dossiers" / "projects" / "Aura" / "dossier.json"

    result = _call(provider, path=AURA, goal="新目标", status="新现状")
    assert result["success"] is True, result

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["goal"] == "新目标"
    assert raw["status"] == "新现状"
    # 只增不改：这一层不认识的键原样留着，不许被写回洗掉。
    assert raw["别人的键"] == {"留着": True}
    # 没点名的格子原样不动。
    assert raw["registry"] == {"state": "active"}
    assert raw["stages"] == PROFILE["stages"]


def test_updated_is_stamped_by_the_code(roots):
    _mind, surface, provider = roots
    path = surface / "dossiers" / "projects" / "Aura" / "dossier.json"

    result = _call(provider, path=AURA, goal="新目标")
    assert result["success"] is True, result
    # 日期由代码盖：模型不填，免得档案的时间停在上一轮。
    today = date.today().isoformat()
    assert result["updated"] == today
    assert json.loads(path.read_text(encoding="utf-8"))["updated"] == today


def test_a_bad_stage_list_writes_nothing(roots):
    _mind, surface, provider = roots
    path = surface / "dossiers" / "projects" / "Aura" / "dossier.json"
    before = path.read_bytes()

    # 缺 title 的那一条 ⇒ 整次调用失败。
    missing_title = _call(
        provider, path=AURA, stages=[{"id": "s1", "title": "好的"}, {"id": "s2"}]
    )
    assert missing_title["success"] is False
    assert "title" in missing_title["error"]
    assert path.read_bytes() == before

    # ``done`` 必须是布尔。
    bad_done = _call(
        provider, path=AURA, stages=[{"id": "s1", "title": "x", "done": "已完成"}]
    )
    assert bad_done["success"] is False
    assert "done" in bad_done["error"]
    assert path.read_bytes() == before

    # 不是数组。
    not_a_list = _call(provider, path=AURA, stages={"s1": "x"})
    assert not_a_list["success"] is False
    assert path.read_bytes() == before


def test_paths_that_are_not_dossier_records_are_refused(roots):
    _mind, _surface, provider = roots

    for path in (
        "Dossiers/projects/Aura/plan.md",
        "Dossiers/projects/Aura",
        "Notes/某篇.md",
        "Mind/wiki/concepts/x.md",
    ):
        result = _call(provider, path=path, goal="x")
        assert result["success"] is False, path
        assert "不是档案本体" in result["error"], (path, result)


def test_the_write_is_readable_again_and_leaves_no_partial_file(roots):
    _mind, surface, provider = roots
    path = surface / "dossiers" / "projects" / "Aura" / "dossier.json"

    result = _call(
        provider,
        path=AURA,
        stages=[{"id": "s1", "title": "第一阶段", "detail": "交付标准", "done": True}],
        log_append="- 今天推了一步。",
    )
    assert result["success"] is True, result

    # 写完能读回来（原子落盘）。
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["stages"] == [
        {"id": "s1", "title": "第一阶段", "detail": "交付标准", "done": True}
    ]
    assert raw["log"].endswith("- 今天推了一步。")
    assert raw["别人的键"] == {"留着": True}
    # 原子落盘：目录里不留临时文件残骸（``.dossier-*``）。
    assert not [p.name for p in path.parent.iterdir() if p.name.startswith(".dossier-")]


def test_a_broken_or_missing_record_is_refused_instead_of_overwritten(roots):
    _mind, surface, provider = roots
    path = surface / "dossiers" / "projects" / "Aura" / "dossier.json"

    path.write_text("{ 这不是 JSON", encoding="utf-8")
    broken = _call(provider, path=AURA, goal="新目标")
    assert broken["success"] is False
    assert "读不出来" in broken["error"]
    # 坏文件不许被写回盖掉。
    assert path.read_text(encoding="utf-8") == "{ 这不是 JSON"

    path.unlink()
    missing = _call(provider, path=AURA, goal="新目标")
    assert missing["success"] is False
    assert "还不存在" in missing["error"]
    assert not path.exists()


def test_a_call_without_any_field_is_refused(roots):
    _mind, surface, provider = roots
    path = surface / "dossiers" / "projects" / "Aura" / "dossier.json"
    before = path.read_bytes()

    empty = _call(provider, path=AURA)
    assert empty["success"] is False
    assert "没有要改的格子" in empty["error"]
    assert path.read_bytes() == before


def test_the_dossier_can_be_named_instead_of_pathed(roots):
    _mind, surface, provider = roots
    path = surface / "dossiers" / "research" / "SRTP" / "dossier.json"

    result = _call(provider, agent="SRTP", status="在写第四章。")
    assert result["success"] is True, result
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "在写第四章。"

    unknown = _call(provider, agent="不存在的档案", status="x")
    assert unknown["success"] is False
    assert "找不到" in unknown["error"]
