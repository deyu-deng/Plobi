"""档案本体（``dossier.json``）这一层：读、写、原子落盘、md 抽取（WP-DOSSIER-SHAPE）。

钉住四件事：**坏文件不许糊成空表**（文件不在 ≠ 文件坏了）、**只增不改**（写回时别人
的键原样留着）、**落盘要原子**、**md 只在迁移时读**（读侧不再看它一眼）。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from plobi.human import dossier_profile as dp

PLAN = textwrap.dedent(
    """\
    ---
    project: Aura
    title: Aura
    type: engineering
    group: product
    cloud: Aura
    status: active
    created: 2026-06-15
    updated: 2026-09-28
    tech: [HarmonyOS, ArkTS]
    summary: 一句摘要。
    ---

    # Aura 气韵

    引子那段不算目标。

    ## Goal

    构建并上架一款情绪追踪应用。

    ## Roadmap

    | # | 阶段 | 交付标准 |
    |---|------|----------|
    | 1 | 核心情绪记录 | 罗素模型落地（已完成） |
    | 2 | 交互打磨 | 流体水母 |
    | 3 | 上架 | 软著提交流程 |

    ## Milestones

    - [x] M1：罗素环形情绪模型调研
    - [ ] M2：软著文档生成

    ## Status

    - 当前阶段：交互打磨
    - 下一步：矢量图标
    """
)

PROGRESS = textwrap.dedent(
    """\
    # Aura · 进度

    ## Log

    - 2026-09-28：建档
    """
)


def test_parse_plan_markdown_pulls_the_four_grids():
    profile = dp.parse_plan_markdown(PLAN, agent="Aura", category="projects")

    assert profile.agent == "Aura"
    assert profile.title == "Aura"
    assert profile.goal == "构建并上架一款情绪追踪应用。"
    assert profile.status == "当前阶段：交互打磨；下一步：矢量图标"
    assert [stage.title for stage in profile.stages] == ["核心情绪记录", "交互打磨", "上架"]
    # 完成态按序对 Milestones 的勾选框：M1 勾了 ⇒ 第一个阶段完成。
    assert [stage.done for stage in profile.stages] == [True, False, False]
    assert profile.milestones_done == 1
    assert profile.milestones_total == 3
    assert profile.registry["state"] == "active"
    assert profile.registry["group"] == "product"
    assert profile.registry["tech"] == ["HarmonyOS", "ArkTS"]
    assert profile.registry["summary"] == "一句摘要。"
    assert profile.updated == "2026-09-28"


def test_parse_progress_markdown_keeps_the_log():
    assert dp.parse_progress_markdown(PROGRESS).startswith("# Aura · 进度")


def test_read_profile_separates_missing_from_broken(tmp_path):
    missing = dp.read_profile(tmp_path / "dossier.json")
    assert missing.exists is False
    assert missing.error is None
    assert missing.profile is None

    broken_path = tmp_path / "dossier.json"
    broken_path.write_text("{ 这不是 JSON", encoding="utf-8")
    broken = dp.read_profile(broken_path)
    assert broken.exists is True
    assert broken.error is not None
    assert broken.profile is None


def test_read_profile_parses_a_written_profile(tmp_path):
    path = tmp_path / "dossier.json"
    profile = dp.DossierProfile(
        agent="Aura",
        goal="目标",
        status="现状",
        stages=[dp.Stage(id="s1", title="第一阶段", detail="交付标准", done=True)],
        registry={"state": "active"},
        updated="2026-09-28",
    )
    dp.write_profile(path, profile)

    loaded = dp.read_profile(path)
    assert loaded.exists is True
    assert loaded.error is None
    assert loaded.profile is not None
    assert loaded.profile.goal == "目标"
    assert loaded.profile.stages[0].title == "第一阶段"
    assert loaded.profile.milestones_done == 1


def test_update_profile_keeps_keys_it_does_not_know(tmp_path):
    path = tmp_path / "dossier.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "agent": "Aura",
                "goal": "旧目标",
                "别人的键": {"留着": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    dp.update_profile(path, dp.DossierProfile(agent="Aura", goal="新目标"))

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["goal"] == "新目标"
    # 只增不改：别人往这份档案里加的东西，不许被我们的写回洗掉。
    assert raw["别人的键"] == {"留着": True}


def test_write_profile_leaves_no_partial_file(tmp_path):
    path = tmp_path / "dossier.json"
    dp.write_profile(path, dp.DossierProfile(agent="Aura", goal="目标"))

    # 原子落盘：目录里不留临时文件残骸（``.dossier-*``）。
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith(".dossier-")]
    assert json.loads(path.read_text(encoding="utf-8"))["agent"] == "Aura"


def test_home_for_agent_resolves_through_the_roster(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    roster = tmp_path / "projects.yaml"
    roster.write_text(
        "version: 1\nagents:\n  Aura:\n    role: principal\n"
        "    category: projects\n    mind_subtree: Dossiers/projects/Aura\n",
        encoding="utf-8",
    )

    assert dp.home_for_agent("Aura", roster) == surface / "dossiers" / "projects" / "Aura"


# ── 粗节点（WP-FIVE-DOSSIERS）────────────────────────────────────────────
# 用户 11-19 22:0x：「一个节点对应一个粗节点……比如某个材料提交截止的时间。
# 什么节点要实现什么东西。」⇒ 节点带两格：**大概什么时候** ＋ **节点性质**；
# 档案上再多一格 **节奏**（butler 那一类专用）。


def test_stage_carries_due_and_kind(tmp_path):
    path = tmp_path / "dossier.json"
    dp.write_profile(
        path,
        dp.DossierProfile(
            agent="Contest",
            category="events",
            stages=[
                dp.Stage(
                    id="n1",
                    title="材料提交截止",
                    due="2026-12-01",
                    kind="deadline",
                )
            ],
        ),
    )

    loaded = dp.read_profile(path)
    stage = loaded.profile.stages[0]

    assert stage.due == "2026-12-01"
    assert stage.kind == "deadline"
    # 落盘的那一份也带着这两格。
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["stages"][0]["due"] == "2026-12-01"
    assert raw["stages"][0]["kind"] == "deadline"


def test_old_profile_without_due_and_kind_still_reads(tmp_path):
    """旧档案**照旧读**：没有那两格就是没有，不是坏。"""
    path = tmp_path / "dossier.json"
    path.write_text(
        json.dumps(
            {
                "agent": "Aura",
                "goal": "旧目标",
                "stages": [{"id": "s1", "title": "第一阶段", "done": False}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = dp.read_profile(path)

    assert loaded.error is None
    assert loaded.profile.stages[0].due == ""
    assert loaded.profile.stages[0].kind == ""


def test_unknown_kind_is_refused_before_anything_is_written(tmp_path):
    """形状不对 ⇒ **落盘前**就红：盘上那一份一个字节都不动。"""
    path = tmp_path / "dossier.json"
    dp.write_profile(path, dp.DossierProfile(agent="Aura", goal="旧目标"))

    try:
        dp.apply_profile_update(
            path,
            stages=[{"id": "n1", "title": "节点", "kind": "随时"}],
        )
    except dp.ProfileError as exc:
        assert "kind" in str(exc)
    else:  # pragma: no cover - 这一条要红
        raise AssertionError("非法的 kind 没有被拦下来")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["goal"] == "旧目标"
    assert raw["stages"] == []


def test_cadence_is_written_and_validated(tmp_path):
    path = tmp_path / "dossier.json"
    dp.write_profile(path, dp.DossierProfile(agent="Housekeeper", category="butler"))

    dp.apply_profile_update(path, cadence="monthly")
    assert dp.read_profile(path).profile.cadence == "monthly"

    try:
        dp.apply_profile_update(path, cadence="每旬")
    except dp.ProfileError as exc:
        assert "cadence" in str(exc)
    else:  # pragma: no cover - 这一条要红
        raise AssertionError("非法的 cadence 没有被拦下来")

    # 非法的那次没有落盘 ⇒ 还是 monthly。
    assert dp.read_profile(path).profile.cadence == "monthly"
