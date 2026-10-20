"""WP-MEMORY-L0-ENABLE —— L0 那层真开着，并按出厂上限 2,200 / 1,375 跑。

裁定 156（2026-10-16）拍的是两件事：**开**，以及**定死下沉条件——被挤出去的条目升 L1**。
这台当时的现读是 ``memory_enabled: false`` / ``user_profile_enabled: false``，
所以「只定上限不定下沉」等于没定。这文件把两条都钉成可复跑的断言。

零额度：不建模型、不打网关，只走被测代码自己的路径。复跑：

    bash scripts/run_tests.sh -j 1 tests/agent/test_memory_l0_overflow.py
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools import memory_tool as mt

# 出厂上限（裁 156 点名的两个数）。这两个常量在这里只用于**造压力**，
# 真值一律从 DEFAULT_CONFIG / 被测代码的解析函数取（不许拿字面数字当判据）。
TEMPLATE = (Path(__file__).resolve().parents[2]
            / "plugins" / "plobi-north-star" / "l1_profile_template.yaml")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / "plobihome"
    root.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(root))
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr(mt, "get_shared_plobi_root", lambda: root)
    return root


def _factory_store(slug="aura"):
    """按 DEFAULT_CONFIG 的上限建 store —— 出厂数字，不是测试自己编的。"""
    from plobi_cli.config import DEFAULT_CONFIG

    limits = mt.memory_limits_from_config(DEFAULT_CONFIG["memory"])
    assert limits["squeeze_pressure"] == DEFAULT_CONFIG["memory"]["squeeze_pressure"]
    store = mt.MemoryStore(slug=slug, **limits)
    store.load_from_disk()
    return store


def _fill_to_pressure(store, target, marker):
    """把某一格塞到「差一条就满」，用来造挤不出去的压力。返回塞进去的条数。"""
    entry = f"{marker} " + "×" * (180 - len(marker) - 1)
    n = 0
    # 序号补零：不补的话 "#1" 是 "#10"／"#11" 的子串，后面按 old_text 定位会撞车。
    while store.add(target, f"{entry} #{n:03d}")["success"]:
        n += 1
        if n > 200:
            raise AssertionError("塞不满：上限这条判据没被压力覆盖到")
    limit = store._char_limit(target)
    assert store._at_capacity(target), "压力没造到位，下面的下沉读数不算"
    return n


# --------------------------------------------------------------------------- #
# ① 开的是那一处真源，不是第二份答案
# --------------------------------------------------------------------------- #

def test_l1_template_is_the_source_and_it_opens_l0():
    """开机塑形把这份模板并进真实配置并落盘 ⇒ 它就是那一处真源。

    现核链条：``agent/agent_init.py`` 读的是 ``load_config()``，而
    ``ensure_l1_secretary_form`` 在启动时把本模板深并进配置再 ``save_config()``——
    模板写 false 的时候，改 ``config.yaml`` 等于下次开机又被写回，所以判据落在模板本身。
    """
    assert TEMPLATE.is_file(), f"模板不在：{TEMPLATE}"
    section = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))["memory"]
    assert section.get("memory_enabled") is True, (
        f"真源仍写着 {section.get('memory_enabled')!r}，L0 那层根本没开")
    assert section.get("user_profile_enabled") is True
    # 开的是总开关；回合后那个自动 review fork 仍关着（那条理由没被推翻）
    assert section.get("nudge_interval") == 0


def test_default_config_and_the_template_give_one_answer():
    """不许出现「出厂说开、模板说关」这种两处各一套的形状。"""
    from plobi_cli.config import DEFAULT_CONFIG

    shipped = DEFAULT_CONFIG["memory"]
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))["memory"]
    for key in ("memory_enabled", "user_profile_enabled"):
        assert shipped.get(key) is True, f"DEFAULT_CONFIG.memory.{key} 不是 true"
        assert template.get(key) == shipped.get(key), (
            f"{key}: 模板 {template.get(key)!r} ≠ 出厂 {shipped.get(key)!r}")


def test_factory_caps_flow_into_the_store_not_from_a_hardcoded_number():
    """上限走配置流（2,200 / 1,375 是裁 156 点的出厂值），三份各自可配。"""
    from plobi_cli.config import DEFAULT_CONFIG

    memory_section = DEFAULT_CONFIG["memory"]
    assert (memory_section["global_char_limit"], memory_section["agent_char_limit"],
            memory_section["user_char_limit"]) == (2200, 2200, 1375)
    store = _factory_store()
    assert store._char_limit("memory") == memory_section["agent_char_limit"]
    assert store._char_limit("global") == memory_section["global_char_limit"]
    assert store._char_limit("user") == memory_section["user_char_limit"]


def test_the_layer_is_actually_live_when_the_flags_are_on(home):
    """开着 ⇒ 三份真进系统提示（这条是「装机硬写 false」翻正后的正面形状）。

    走真实消费方 ``build_system_prompt_parts``，不是自己复述一遍注入逻辑。
    """
    from agent.system_prompt import build_system_prompt_parts

    store = _factory_store()
    store.add("memory", "这台机的后端测试入口只有 scripts/run_tests.sh")
    store.add("user", "用户要大白话，术语当场翻译")

    reloaded = _factory_store()
    agent = SimpleNamespace(
        load_soul_identity=False, skip_context_files=True, valid_tool_names=[],
        platform="", active_profile="default",
        _task_completion_guidance=False, _tool_use_enforcement=False,
        _environment_probe=False, _kanban_worker_guidance="",
        _memory_store=reloaded, _memory_enabled=True, _user_profile_enabled=True,
        _memory_manager=None, _platform_hint_overrides={},
        model="", provider="", pass_session_id=False, session_id="",
    )
    volatile = build_system_prompt_parts(agent)["volatile"]
    assert "scripts/run_tests.sh" in volatile
    assert "术语当场翻译" in volatile


# --------------------------------------------------------------------------- #
# ② 挤出来的条目真升 L1（已有的待确认层），读数可复跑
# --------------------------------------------------------------------------- #

def test_squeezed_entries_land_in_l1_and_not_in_the_void(home):
    """满格时为了让位而离开 L0 的条目 ⇒ 全部进待确认层，一条都不许静默丢。

    读数两问（裁 156 与判据②要的就是这两个）：**挤出几条**、**升到哪**。
    """
    from tools import write_approval as wa

    store = _factory_store()
    _fill_to_pressure(store, "memory", "本机事实")
    victim_one, victim_two = store.memory_entries[0], store.memory_entries[1]
    assert victim_one != victim_two and victim_one not in victim_two

    result = store.apply_batch("memory", [
        {"action": "remove", "old_text": victim_one},
        {"action": "remove", "old_text": victim_two},
        {"action": "add", "content": "合并后的那条更短的事实"},
    ])
    assert result["success"] is True, result

    squeezed = wa.list_pending(wa.MEMORY)
    assert result["squeezed_to_l1"] == 2, result
    assert len(squeezed) == 2, "读数：挤出去 2 条"
    for record in squeezed:
        payload = record["payload"]
        assert payload["target"] == "global", "读数：升到共享那一层（经人批）"
        assert payload["squeezed_from"].startswith("memory:")
        assert len(payload["base_sha256"]) == 64, "dry-run 产物要带 sha256"
    assert {r["payload"]["content"] for r in squeezed} == {victim_one, victim_two}

    # 未批 ⇒ 共享那一层一个字没变：下沉是递候选，不是自动晋升
    assert not (home / "memory" / "GLOBAL.md").exists()

    # 人批之后才真进去（走已有的 approve 回放口）
    first = squeezed[0]["payload"]
    assert mt.apply_memory_pending(first, _factory_store())["success"] is True
    assert first["content"] in (home / "memory" / "GLOBAL.md").read_text(encoding="utf-8")


def test_deliberate_delete_does_not_pose_as_a_squeeze(home):
    """有位置时的删除是「这条错了，删掉」，不是被挤出去 ⇒ 不许灌进待确认层。

    裁 156 定的是**下沉条件**，所以反面也得钉住：否则那道闸会被普通编辑灌满，
    「人点头」就变成走形式。
    """
    from tools import write_approval as wa

    store = _factory_store()
    store.add("memory", "一条已经过时的本机事实")
    store.add("memory", "另一条还用的事实")
    assert not store._at_capacity("memory")

    assert store.remove("memory", "已经过时")["success"] is True
    assert wa.list_pending(wa.MEMORY) == []


def test_rewording_that_loses_nothing_is_not_a_squeeze(home):
    """满格时的改写如果没把信息改短，就没有东西被挤出去 ⇒ 不下沉。"""
    from tools import write_approval as wa

    store = _factory_store()
    _fill_to_pressure(store, "memory", "本机事实")
    victim = store.memory_entries[0]
    assert store.replace("memory", victim, victim + "（补一句）")["success"] is True
    assert wa.list_pending(wa.MEMORY) == []


def test_the_top_layer_and_the_digest_have_nowhere_up_to_go(home):
    """``global`` 上面没有另一层，``status`` 是给 L1 看的摘要 ⇒ 两者都不下沉。"""
    from tools import write_approval as wa

    store = _factory_store()
    assert store.sink_displaced("global", ["全局的一条"], reason="remove") == 0
    assert store.sink_displaced("status", ["一条近况"], reason="remove") == 0
    assert store.sink_displaced("memory", ["自己那份的一条"], reason="remove") == 1
    assert len(wa.list_pending(wa.MEMORY)) == 1


def test_sinking_never_rewrites_the_user_profile_or_deletes_anything(home):
    """硬边界：不改历史画像、不删既有条目 —— 下沉只递候选，共享那份由人批才动。

    画像（USER.md）里被挤出去的那条同样只进待确认层；画像文件本身不多写一个字，
    共享那一层在批准之前不存在。
    """
    from tools import write_approval as wa

    store = _factory_store()
    _fill_to_pressure(store, "user", "用户偏好")
    victim = store.user_entries[0]
    profile_bytes = (home / "memory" / "USER.md").read_bytes()

    assert store.remove("user", victim)["success"] is True
    pending = wa.list_pending(wa.MEMORY)
    assert len(pending) == 1
    assert pending[0]["payload"]["target"] == "global"
    assert pending[0]["payload"]["content"] == victim
    # 画像文件只有「那一条被删掉」这一处变化，别的字节没被动过
    after = (home / "memory" / "USER.md").read_text(encoding="utf-8")
    assert victim not in after
    assert profile_bytes.decode("utf-8").replace(victim, "").count("用户偏好") == after.count("用户偏好")
    assert not (home / "memory" / "GLOBAL.md").exists()
