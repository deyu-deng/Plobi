"""裁定 149：L1 排明天，代码只给材料、校边界、退回重排。

两条测试，钉的都是**行为/关系**，不是数值：

1. 不合格就**退回去重问**，代码不许顺手改数据（不裁时间、不挪条目、不删条目）；
   问题清单原样带回给 L1；缺「为什么这么排」（149④ 的留痕）也算不合格。
2. 「相邻两项之间只够一次步行通勤的那种间隔，不许再写一条通勤占掉它」写成**关系判据**
   （通勤条目 ≥ 它两侧那段空档 ⇒ 违规），不是"通勤超过 N 分钟"那种阈值——
   写死分钟数正是 149 要否掉的那个形状。

全程零额度：`llm_call` 是假函数，不碰任何模型，也不碰真 agenda.db。
"""

from __future__ import annotations

import copy
import json

import pytest

from plobi.agenda import llm_planning as lp

DAY = "2026-10-16"


def _item(title: str, start: str, end: str) -> dict:
    return {"title": title, "start_at": f"{DAY}T{start}:00", "end_at": f"{DAY}T{end}:00"}


@pytest.fixture()
def fake_planner(monkeypatch):
    """把材料口与库连接都换成假的：这两条测试不许碰真 agenda.db，更不许碰模型。"""
    materials = {
        "for_date": DAY,
        "classes": [], "deadlines": [], "other_events": [],
        "today_items": [], "avoid_windows": [], "project_pace": [],
        "yesterday": {"status": "missing", "summary": "昨夜未生成计划"},
        "missing": ["昨天的完成情况（plan_items 还没有状态列，R-099 在落）"],
    }
    monkeypatch.setattr(lp, "plan_materials", lambda conn, day: materials)
    return materials


def test_a_bad_proposal_is_sent_back_untouched_with_its_own_problems(fake_planner):
    """149②「校验不过退回重排，不许静默改数据」+ 149④ 的留痕。

    假 L1 第一版双订了一段（11:30–13:00 与 12:00–12:45 叠在一起）、也没写「为什么这么排」；
    第二版两样都改对。断言四件事：被退回去重问（两次调用）· 两个问题都原样带回去了 ·
    **第一版那批条目一个字段都没被代码动过** · 第二版才通过。
    """
    bad = [
        _item("写 Resonote 的 M1", "11:30", "13:00"),
        _item("午饭", "12:00", "12:45"),
    ]
    bad_snapshot = copy.deepcopy(bad)
    good = [
        _item("写 Resonote 的 M1", "09:00", "11:00"),
        _item("午饭", "12:00", "12:45"),
    ]
    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        """第二版故意用**真回包的形状**（2026-10-16 那一发实测）：一句人话 + ```json 围栏 +
        `schedule` / `time` / `end` 这套别名键。解析器只认契约形状的话，排得再好也会被扔掉——
        那就是我们上一版的真状态（块数 0，理由写成"没有任何能落的时间块"）。"""
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps({"items": bad})  # 双订，且没交 reason
        return (
            "好的，按规则排完这一天。\n\n```json\n"
            + json.dumps({"schedule": [{"time": it["start_at"][11:16], "end": it["end_at"][11:16],
                                        "title": it["title"]}
                                       for it in good],
                          "reason": "上午脑子最清楚，把最难的放前面"}, ensure_ascii=False)
            + "\n```"
        )

    proposal = lp.plan_with_llm(DAY, fake_llm, conn=object())

    assert len(prompts) == 2, "第一版就该被打回，不是直接落库"
    assert "双订" in prompts[1] and "为什么这么排" in prompts[1], prompts[1]
    assert bad == bad_snapshot, "代码不许动 L1 交回来的条目"
    assert [row["title"] for row in proposal.items] == [row["title"] for row in good]
    assert proposal.reason.startswith("上午脑子最清楚") and proposal.attempts == 2


def test_a_commute_that_fills_the_gap_is_a_violation_and_a_longer_gap_is_not(fake_planner):
    """149③ 的关系判据：通勤条目**占满**相邻两项之间那段空档 ⇒ 违规；
    空档比通勤长（路上还有富余）⇒ 不违规。断言里不出现任何分钟数。"""
    fills = [  # 两节课之间没有富余：那段空档就是通勤本身
        _item("上大学物理", "09:00", "10:00"),
        _item("通勤去实验室", "10:00", "10:01"),
        _item("大学物理实验", "10:01", "12:00"),
    ]
    fits = [  # 中间留出了真正的富余：通勤只吃掉自己那一段
        _item("上大学物理", "09:00", "10:00"),
        _item("通勤去实验室", "10:00", "10:10"),
        _item("大学物理实验", "11:30", "12:00"),
    ]

    tight = lp.validate_proposal(fills)
    loose = lp.validate_proposal(fits)

    assert any("通勤" in v and "空档" in v for v in tight), tight
    assert not any("通勤" in v and "空档" in v for v in loose), loose
