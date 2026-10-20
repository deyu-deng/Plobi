"""Three-part memory shape (裁定 181): GLOBAL + per-agent + USER, plus the L1 digest.

These are the 判据 of 任务书第 2 步 (``Docs/tasks/wp-profile-cancel-all-task.md``),
each pinned to a runnable assertion:

  ① 两个 Agent 各写一条，互相读不到对方那份，但都读得到 GLOBAL.md
  ② 晋升要人批：未批的候选不进 GLOBAL.md，dry-run 产物带 sha256
  ③ 注入合计等于三份之和（走真正的 system-prompt 消费方，不止测函数）
  ④ L1 读得到各 Agent 的 STATUS 摘要，读不到任何 Agent 的 MEMORY.md 全文
  ⑤ 三份上限来自三个配置键；读路径不再按分身家解析

Everything runs against a temp ``PLOBI_HOME`` through the code's own resolvers —
no hand-built paths, no gateway, zero quota.
"""

import json
from types import SimpleNamespace

import pytest

from tools import memory_tool as mt


@pytest.fixture()
def mem_root(tmp_path, monkeypatch):
    """A fake install: PLOBI_HOME == the shared root (no profiles involved)."""
    root = tmp_path / "plobihome"
    root.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(root))
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr(mt, "get_shared_plobi_root", lambda: root)
    return root


def _store_for(slug, **limits):
    """Build + load a store for one agent identity, the way agent_init does."""
    kwargs = {"memory_char_limit": 2200, "user_char_limit": 1375,
              "global_char_limit": 2200, "status_char_limit": 200,
              "l1_status_char_limit": 1500}
    kwargs.update(limits)
    store = mt.MemoryStore(slug=slug, **kwargs)
    store.load_from_disk()
    return store


def _fake_agent(store, **overrides):
    """A stand-in shaped like the real prompt builder expects.

    Same attribute set as ``tests/agent/test_platform_hint_desktop._make_agent``:
    判据③ 要的是「拿一次真实 prompt 快照数出来」，所以这里走真的
    ``build_system_prompt_parts``，不复制它的逻辑。
    """
    base = dict(
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=[],
        platform="",
        active_profile="default",
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=store,
        _memory_enabled=True,
        _user_profile_enabled=True,
        _memory_manager=None,
        _platform_hint_overrides={},
        model="",
        provider="",
        pass_session_id=False,
        session_id="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# 布局：三份住在共享根下，按 Agent 身份分持
# --------------------------------------------------------------------------- #

def test_layout_is_shared_root_not_the_active_home(mem_root):
    """GLOBAL / per-agent MEMORY / STATUS / USER all live under ``<共享根>/memory``."""
    store = _store_for("aura")
    memory_root = mem_root / "memory"
    assert store._path_for("global") == memory_root / "GLOBAL.md"
    assert store._path_for("user") == memory_root / "USER.md"
    assert store._path_for("memory") == memory_root / "agents" / "aura" / "MEMORY.md"
    assert store._path_for("status") == memory_root / "agents" / "aura" / "STATUS.md"
    # 另一家的路径跟着身份走，不跟着家走
    assert _store_for("nuclide")._path_for("memory") == (
        memory_root / "agents" / "nuclide" / "MEMORY.md")


def test_every_scope_stays_inside_the_memory_root(mem_root):
    store = _store_for("aura")
    for scope in ("global", "memory", "status", "user"):
        assert store._path_for(scope).is_relative_to(mem_root / "memory")


def test_profile_home_never_resolves_a_second_ledger(mem_root, monkeypatch):
    """判据⑤：记忆不再按分身家解析，分身家那个残留的 ``memories/`` 没人读。

    ``PLOBI_HOME`` 指到 ``<root>/profiles/<name>`` 时读路径必须回到共享根那一份；
    存量那些 ``memories/`` 原地留着（本刀一个字节不碰），但不再被读。
    """
    profile = mem_root / "profiles" / "l2-course-ee"
    (profile / "memories").mkdir(parents=True)
    (profile / "memories" / "MEMORY.md").write_text("另一个分身的残留", encoding="utf-8")
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    store = _store_for("l2-course-ee")
    assert store._path_for("memory") == (
        mem_root / "memory" / "agents" / "l2-course-ee" / "MEMORY.md")
    assert not str(store._path_for("memory")).startswith(str(profile))
    # 存量那一份原样在那儿，且读不到它
    assert (profile / "memories" / "MEMORY.md").read_text(encoding="utf-8") == "另一个分身的残留"
    assert "另一个分身的残留" not in (store.format_for_system_prompt("memory") or "")


# --------------------------------------------------------------------------- #
# ① 各 Agent 读写自己那份，GLOBAL 与 USER 共用
# --------------------------------------------------------------------------- #

def test_two_agents_do_not_see_each_other_but_share_global(mem_root):
    aura = _store_for("aura")
    course = _store_for("l2-course-aerobics")

    assert aura.add("memory", "Aura 只知道自己那条")["success"] is True
    assert course.add("memory", "课程分身只知道自己那条")["success"] is True
    aura.add("global", "跨 Agent 都成立的一条")

    aura_block = _store_for("aura").format_for_system_prompt("memory") or ""
    course_block = _store_for("l2-course-aerobics").format_for_system_prompt("memory") or ""

    assert "Aura 只知道自己那条" in aura_block
    assert "课程分身只知道自己那条" not in aura_block
    assert "课程分身只知道自己那条" in course_block
    # 两份都读得到全局那一层
    assert "跨 Agent 都成立的一条" in aura_block
    assert "跨 Agent 都成立的一条" in course_block
    # 画像仍一份
    assert aura._path_for("user") == course._path_for("user")


def test_status_and_memory_writes_are_separate_files(mem_root):
    store = _store_for("aura")
    store.add("status", "在做课表")
    store.add("memory", "课表这条规则的依据")
    own = mem_root / "memory" / "agents" / "aura"
    assert "在做课表" in (own / "STATUS.md").read_text(encoding="utf-8")
    assert "在做课表" not in (own / "MEMORY.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# ② 晋升走已有的待确认层：未批不进 GLOBAL.md，dry-run 带 sha256
# --------------------------------------------------------------------------- #

def test_writing_the_global_scope_stages_with_hashes_and_writes_nothing(mem_root):
    """判据②：晋升要人批 —— 未批的候选一个字都不进 GLOBAL.md，dry-run 产物带 sha256。

    形状按裁 185 摆：模型递的还是「把这条写进这个作用域」这一件原子事，
    ``action`` 仍是 add／replace／remove，**没有 promote 那种要它猜对的分支**；
    要人点头是 ``global`` 这个位置自己的规则。
    """
    from tools import write_approval as wa

    store = _store_for("aura")
    out = mt.memory_tool(
        action="add", target="global",
        content="这台机的后端测试入口只有 scripts/run_tests.sh",
        source="vault/projects/Plobi/plan.md",
        store=store,
    )
    result = json.loads(out)
    assert result["staged"] is True
    assert len(result["base_sha256"]) == 64, "dry-run 要带上当前 GLOBAL.md 的 sha256"
    assert len(result["plan_sha256"]) == 64

    assert not (mem_root / "memory" / "GLOBAL.md").exists(), "未批的候选不许进 GLOBAL.md"
    pending = wa.list_pending(wa.MEMORY)
    assert [r["id"] for r in pending] == [result["pending_id"]]
    payload = pending[0]["payload"]
    assert payload["target"] == "global" and payload["action"] == "add"
    assert "run_tests.sh" in payload["content"]
    assert payload["base_sha256"] == result["base_sha256"]

    # 人批之后才落 GLOBAL.md —— 同一个 store、同一条路径。
    assert mt.apply_memory_pending(payload, store)["success"] is True
    written = (mem_root / "memory" / "GLOBAL.md").read_text(encoding="utf-8")
    assert "run_tests.sh" in written
    assert "出处 vault/projects/Plobi/plan.md" in written


def test_global_stays_approval_only_even_with_the_write_gate_off(mem_root):
    """``memory.write_approval`` 关着（出厂默认）也不许把全局那一格写成直写。

    那道闸管的是自己那份／画像要不要每次都问；全局那一份的规则独立且恒定：
    计划 + 哈希 + 点头。这里断的是「闸关着时仍然只落待确认层」。
    """
    from tools import write_approval as wa

    assert wa.write_approval_enabled(wa.MEMORY) is False, (
        "本条测的是默认关闸的形状；把闸开着去测就测不到东西了"
    )
    store = _store_for("aura")
    result = json.loads(mt.memory_tool(action="add", target="global",
                                       content="跨 Agent 都成立的一条", store=store))
    assert result["staged"] is True, f"关闸时全局写被直放：{result}"
    assert not (mem_root / "memory" / "GLOBAL.md").exists()


def test_batch_shape_on_the_global_scope_stages_as_one_record(mem_root):
    from tools import write_approval as wa

    store = _store_for("aura")
    result = json.loads(mt.memory_tool(
        target="global",
        operations=[{"action": "add", "content": "全局第一条"},
                    {"action": "add", "content": "全局第二条"}],
        store=store,
    ))
    assert result["staged"] is True
    pending = wa.get_pending(wa.MEMORY, result["pending_id"])
    assert pending["payload"]["action"] == "batch"
    assert len(pending["payload"]["operations"]) == 2
    assert not (mem_root / "memory" / "GLOBAL.md").exists()


def test_tool_surface_stays_atomic_with_four_data_locations(mem_root):
    """裁 185：``action`` 只有 add／replace／remove；``target`` 是四个数据位置。

    多一个"业务分支"参数就多一处模型必须猜对才走得对的地方（10-17 那颗 P0 的成因）。
    """
    props = mt.MEMORY_SCHEMA["parameters"]["properties"]
    assert props["action"]["enum"] == ["add", "replace", "remove"]
    assert set(props["target"]["enum"]) == {"memory", "user", "status", "global"}
    assert set(mt.MEMORY_SCOPES) == {"global", "memory", "status", "user"}
    assert "source" in props, "出处指针是这三份能只留 2200 字的唯一理由"

    store = _store_for("aura")
    result = json.loads(mt.memory_tool(action="add", target="project",
                                       content="按项目分？", store=store))
    assert result["success"] is False


def test_approved_promotion_is_refused_when_global_moved_since(mem_root):
    """批准绑的是它当时看到的那份 GLOBAL.md。

    排队期间全局那一份被别的批准动过 ⇒ 这一笔过期，拒掉，不盖别人写进去的内容
    （同 ``plobi/mind/proposal.py`` 那条「each change carries the old bytes」）。
    """
    store = _store_for("aura")
    payload = {
        "action": "add", "target": "global", "content": "旧世界的一条事实",
        "base_sha256": mt._file_sha(store._path_for("global")),
    }
    path = store._path_for("global")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("别人已经批进去的新内容", encoding="utf-8")

    refused = mt.apply_memory_pending(payload, _store_for("aura"))
    assert refused["success"] is False and "changed since" in refused["error"]
    assert path.read_text(encoding="utf-8") == "别人已经批进去的新内容"


# --------------------------------------------------------------------------- #
# ③ 注入合计 ＝ 三份之和（走真正的 system-prompt 消费方）
# --------------------------------------------------------------------------- #

def test_injected_memory_block_equals_the_three_parts(mem_root):
    from agent.system_prompt import build_system_prompt_parts

    store = _store_for("aura")
    store.add("global", "全局那一份的内容")
    store.add("memory", "本 Agent 那一份的内容")
    store.add("user", "画像那一份的内容")
    store.add("status", "我这几天的近况")

    reloaded = _store_for("aura")
    global_part = reloaded._render_block("global", reloaded.global_entries)
    own_part = reloaded._render_block("memory", reloaded.memory_entries)
    injected = reloaded.format_for_system_prompt("memory")

    # 合计＝全局 + 本 Agent（不多不少；L2 自己那份 STATUS 不并进注入）
    assert injected == f"{global_part}\n\n{own_part}"
    assert len(injected) == len(global_part) + len(own_part) + 2
    assert "我这几天的近况" not in injected

    volatile = build_system_prompt_parts(_fake_agent(reloaded))["volatile"]
    for text in ("全局那一份的内容", "本 Agent 那一份的内容", "画像那一份的内容"):
        assert volatile.count(text) == 1, text
    assert "我这几天的近况" not in volatile


# --------------------------------------------------------------------------- #
# ④ L1 读 STATUS 摘要，绝不读任何 Agent 的 MEMORY.md 全文（钉住这一条）
# --------------------------------------------------------------------------- #

def test_l1_reads_status_digests_never_other_agents_memory(mem_root):
    markers = {}
    for slug in ("aura", "l2-course-ee"):
        store = _store_for(slug)
        markers[slug] = f"{slug}-PRIVATE-FULLTEXT-9C31"
        store.add("memory", f"{markers[slug]} 这一条只有它自己该看见")
        store.add("status", f"{slug} 现在在做那一块课表")

    block = _store_for(mt.L1_SLUG).format_for_system_prompt("memory") or ""

    assert "AGENT STATUS DIGEST" in block
    assert "aura 现在在做那一块课表" in block
    assert "l2-course-ee 现在在做那一块课表" in block
    for marker in markers.values():
        assert marker not in block, f"L1 读到了某个 Agent 的 MEMORY.md 全文：{marker}"
    # 而每个 Agent 自己当然读得到自己那份
    assert markers["aura"] in (_store_for("aura").format_for_system_prompt("memory") or "")


def test_l1_status_digest_is_capped_by_its_own_budget(mem_root):
    for i in range(6):
        store = _store_for(f"agent-{i}")
        store.add("status", f"第 {i} 家的近况" + "×" * 80)

    block = _store_for(mt.L1_SLUG, l1_status_char_limit=200).format_for_system_prompt("memory") or ""
    assert "其余 Agent 的近况这次没列" in block
    # 摘要按总上限截，不随 Agent 数量线性膨胀
    assert len(block.split("AGENT STATUS DIGEST", 1)[1]) < 500


def test_l1_does_not_read_its_own_status_back_at_itself(mem_root):
    store = _store_for(mt.L1_SLUG)
    store.add("status", "L1 自己的近况")
    store.add("memory", "L1 自己的记忆")
    block = _store_for(mt.L1_SLUG).format_for_system_prompt("memory") or ""
    assert "L1 自己的记忆" in block
    assert "L1 自己的近况" not in block


# --------------------------------------------------------------------------- #
# source 指针：memory 指到 vault 的哪一页给人看（方向不可反，裁 182）
# --------------------------------------------------------------------------- #

def test_source_pointer_lands_in_the_entry_with_a_date(mem_root):
    store = _store_for("aura")
    out = mt.memory_tool(action="add", target="memory", content="课表按 calendar_name 全等",
                         source="vault/projects/Plobi/plan.md", store=store)
    assert json.loads(out)["success"] is True
    text = store._path_for("memory").read_text(encoding="utf-8")
    assert "〔出处 vault/projects/Plobi/plan.md · " in text
    assert "〕" in text


def test_source_pointer_works_in_the_batch_shape_too(mem_root):
    store = _store_for("aura")
    out = mt.memory_tool(
        target="memory", source="mind/wiki/plobi.md",
        operations=[{"action": "add", "content": "一条批量的结论"},
                    {"action": "add", "content": "另一条批量的结论"}],
        store=store,
    )
    assert json.loads(out)["success"] is True
    assert store._path_for("memory").read_text(encoding="utf-8").count(
        "出处 mind/wiki/plobi.md") == 2


def test_absolute_or_traversing_source_is_not_turned_into_a_pointer():
    assert mt.entry_with_source("结论", "/etc/passwd") == "结论"
    assert mt.entry_with_source("结论", "vault/../secrets.md") == "结论"
    assert mt.entry_with_source("结论", r"C:\vault\x.md") == "结论"
    assert mt.entry_with_source("结论", None) == "结论"


# --------------------------------------------------------------------------- #
# 三份上限来自三个配置键（不许硬编）
# --------------------------------------------------------------------------- #

def test_three_limits_come_from_three_config_keys():
    """三份上限来自三个键（裁 181「各自可配、不许硬编」），不是共用一个数字。"""
    limits = mt.memory_limits_from_config({
        "global_char_limit": 111,
        "agent_char_limit": 222,
        "user_char_limit": 333,
        "status_char_limit": 44,
        "l1_status_char_limit": 555,
        "squeeze_pressure": 0.5,
    })
    assert limits["global_char_limit"] == 111, "全局那一份"
    assert limits["memory_char_limit"] == 222, "本 Agent 那一份（配置键是 agent_char_limit）"
    assert limits["user_char_limit"] == 333, "画像那一份"
    assert limits["status_char_limit"] == 44
    assert limits["l1_status_char_limit"] == 555
    assert limits["squeeze_pressure"] == 0.5
    assert set(limits) == {"global_char_limit", "memory_char_limit", "user_char_limit",
                           "status_char_limit", "l1_status_char_limit", "squeeze_pressure"}


def test_limits_fall_back_to_defaults_on_junk_config():
    limits = mt.memory_limits_from_config({"agent_char_limit": "not-a-number"})
    assert limits["memory_char_limit"] == 2200
    assert limits["global_char_limit"] == 2200


def test_store_honours_per_scope_limits(mem_root):
    store = _store_for("aura", global_char_limit=40, status_char_limit=30)
    assert store._char_limit("global") == 40
    assert store._char_limit("status") == 30
    assert store._char_limit("memory") == 2200
    assert store._char_limit("user") == 1375
    over = store.add("global", "x" * 60)
    assert over["success"] is False
    assert "exceed the limit" in over["error"]


def test_default_config_declares_the_three_keys():
    from plobi_cli.config import DEFAULT_CONFIG

    memory = DEFAULT_CONFIG["memory"]
    for key in ("global_char_limit", "agent_char_limit", "user_char_limit",
                "status_char_limit", "l1_status_char_limit"):
        assert key in memory, f"memory.{key} missing from DEFAULT_CONFIG"
    assert (memory["global_char_limit"], memory["agent_char_limit"],
            memory["user_char_limit"]) == (2200, 2200, 1375)
    # 上一代那一格被拆成三份，不再由一个键同时管两个作用域
    assert "memory_char_limit" not in memory


def test_slug_resolution_prefers_the_roster_identity(mem_root, monkeypatch):
    """身份来自名册行（``PLOBI_AGENT_ID``），空串＝L1 —— 与裁定 180 的 agent_id 同口径。"""
    monkeypatch.setenv("PLOBI_AGENT_ID", "Aura")
    assert mt.resolve_agent_slug() == "aura"
    monkeypatch.setenv("PLOBI_AGENT_ID", "")
    assert mt.resolve_agent_slug() == mt.L1_SLUG
    # 名字里带路径分隔符也逃不出 agents/ 那一层
    monkeypatch.setenv("PLOBI_AGENT_ID", "../escape")
    assert mt.resolve_agent_slug() == "escape"
