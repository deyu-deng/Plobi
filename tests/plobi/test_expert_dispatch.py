"""临时专家的四类角色（WP-L3-EXPERTS 第二拍）：任务里给 expert，职责与工具面自动展开。

不 spawn 子智能体：只钉「预设怎么展开」与「角色侧记怎么落」这两件纯逻辑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import expert_roles  # noqa: E402
from tools.delegate_tool import _apply_expert_presets, _expert_body  # noqa: E402


def test_the_four_roles_have_their_own_toolsets_and_duty_lines():
    rows = {row["key"]: row for row in expert_roles.role_rows()}

    assert set(rows) == {"file", "browser", "software", "computer"}
    assert rows["file"]["toolsets"] == ["file"]
    assert rows["browser"]["toolsets"] == ["browser", "web"]
    assert all(row["duty"] for row in rows.values())


def test_expert_preset_prefixes_the_duty_and_narrows_the_toolsets():
    tasks = [{"goal": "把三份资料折成一份汇总", "expert": "file"}]

    assert _apply_expert_presets(tasks) == ""
    assert "你是文件与资料专家" in tasks[0]["goal"]
    assert tasks[0]["goal"].rstrip().endswith("把三份资料折成一份汇总")
    assert tasks[0]["toolsets"] == ["file"]
    # 11-04 用户：「L3 也可以自己调用普通子代理」——专家是 orchestrator，不是叶子。
    assert tasks[0]["role"] == "orchestrator"


def test_the_stamped_goal_still_reads_back_as_the_humans_sentence():
    tasks = [{"goal": "把三份资料折成一份汇总", "expert": "file"}]

    _apply_expert_presets(tasks)

    assert _expert_body(tasks[0]) == "把三份资料折成一份汇总"


def test_a_task_without_expert_is_untouched():
    tasks = [{"goal": "普通的一段活"}]

    assert _apply_expert_presets(tasks) == ""
    assert tasks[0] == {"goal": "普通的一段活"}


def test_an_unknown_role_is_refused_with_the_four_names():
    tasks = [{"goal": "x", "expert": "god"}]

    error = _apply_expert_presets(tasks)

    assert "file" in error and "browser" in error


def test_the_role_sidecar_is_written_for_the_rail(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))

    expert_roles.record_role("kid-9", "browser", "抄下课程页的截止时间")

    payload = json.loads((tmp_path / "experts" / "kid-9.json").read_text(encoding="utf-8"))
    assert payload["role"] == "browser"
    assert payload["goal"] == "抄下课程页的截止时间"


# ── 单任务模式的 expert（11-04 实测漏掉的那一条） ─────────────────────────
# 模型写 `delegate_task(goal=..., expert="software")` 时，expert 必须真的落成
# 职责 + 工具面。曾经两处派工入口都不读这个键，结果：子智能体拿父的工具面
# （普通子代理没有 terminal），侧记也不写，左栏把它当普通子智能体。


def _mock_parent():
    import threading
    from unittest.mock import MagicMock

    parent = MagicMock()
    parent.base_url = "https://openrouter.ai/api/v1"
    parent.api_key = "***"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "anthropic/claude-sonnet-4"
    parent.platform = "cli"
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    return parent


def test_single_task_expert_reaches_the_child(monkeypatch):
    """goal + expert（不带 tasks）时，子智能体拿到角色工具面与盖章的任务书。"""
    from types import SimpleNamespace

    import tools.delegate_tool as dt

    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            session_id="kid-1",
            enabled_toolsets=kwargs.get("toolsets"),
            tool_progress_callback=None,
        )

    def fake_run(task_index, goal, child=None, parent_agent=None, expert=None, **_kw):
        return {
            "task_index": task_index,
            "status": "completed",
            "summary": "ok",
            "api_calls": 1,
            "duration_seconds": 0.1,
        }

    monkeypatch.setattr(dt, "_build_child_agent", fake_build)
    monkeypatch.setattr(dt, "_run_single_child", fake_run)

    json.loads(dt.delegate_task(goal="跑一遍构建脚本", expert="software", parent_agent=_mock_parent()))

    assert captured["expert"] == "software"
    assert captured["toolsets"] == ["terminal", "file"]
    assert "你是软件与脚本专家" in captured["goal"]
    assert captured["goal"].rstrip().endswith("跑一遍构建脚本")


def test_single_task_without_expert_stays_a_plain_worker(monkeypatch):
    """没给 expert 的普通派工一点不受影响：不盖章、不窄化工具面。"""
    from types import SimpleNamespace

    import tools.delegate_tool as dt

    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(session_id="kid-2", tool_progress_callback=None)

    monkeypatch.setattr(dt, "_build_child_agent", fake_build)
    monkeypatch.setattr(
        dt,
        "_run_single_child",
        lambda *a, **kw: {
            "task_index": 0, "status": "completed",
            "summary": "ok", "api_calls": 1, "duration_seconds": 0.1,
        },
    )

    json.loads(dt.delegate_task(goal="查一个数", parent_agent=_mock_parent()))

    assert captured["expert"] is None
    assert captured["toolsets"] is None
    assert captured["goal"] == "查一个数"


def test_dispatch_entry_forwards_the_top_level_expert(monkeypatch):
    """`AIAgent._dispatch_delegate_task` 是真实派工路径，必须把 expert 透传下去。"""
    import tools.delegate_tool as dt

    seen: dict = {}

    def fake_delegate(**kwargs):
        seen.update(kwargs)
        return "{}"

    monkeypatch.setattr(dt, "delegate_task", fake_delegate)

    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._delegate_depth = 0
    AIAgent._dispatch_delegate_task(agent, {"goal": "跑脚本", "expert": "computer"})

    assert seen["expert"] == "computer"


def test_the_schema_advertises_expert_on_the_single_task_mode():
    """单任务模式必须能看见 expert，否则模型只能往 tasks[] 里塞。"""
    from tools.delegate_tool import DELEGATE_TASK_SCHEMA

    props = DELEGATE_TASK_SCHEMA["parameters"]["properties"]

    assert props["expert"]["enum"] == ["file", "browser", "software", "computer"]


# ── 专家是 L3：独立工具 + 自己的模型（用户 11-04） ────────────────────────


def test_delegate_expert_is_its_own_tool_in_the_delegation_toolset():
    """专家不是 delegate_task 的一个参数了——它有自己的工具，工具集里看得见。"""
    import tools.delegate_tool  # noqa: F401  触发注册
    from tools.registry import registry

    entry = registry.get_entry("delegate_expert")

    assert entry is not None
    assert entry.toolset == "delegation"
    props = entry.schema["parameters"]["properties"]
    assert props["role"]["enum"] == ["file", "browser", "software", "computer"]
    assert entry.schema["parameters"]["required"] == ["role", "goal"]


def test_delegate_expert_hands_role_and_model_down(monkeypatch):
    """role / model 必须真的进到那条 task 里，否则专家又变回普通子代理。"""
    import tools.delegate_tool as dt

    seen: dict = {}

    def fake_delegate(**kwargs):
        seen.update(kwargs)
        return "{}"

    monkeypatch.setattr(dt, "delegate_task", fake_delegate)

    dt.delegate_expert(
        role="software",
        goal="跑一遍构建脚本",
        context="Windows，git-bash",
        model="workbuddy/deepseek-v4.1-flash",
        parent_agent=_mock_parent(),
    )

    task = seen["tasks"][0]
    assert task["expert"] == "software"
    assert task["goal"] == "跑一遍构建脚本"
    assert task["context"] == "Windows，git-bash"
    assert task["model"] == "workbuddy/deepseek-v4.1-flash"


def test_delegate_expert_refuses_an_unknown_role_and_an_empty_goal():
    import tools.delegate_tool as dt

    bad_role = json.loads(dt.delegate_expert(role="god", goal="x", parent_agent=_mock_parent()))
    assert "file" in bad_role["error"]

    no_goal = json.loads(dt.delegate_expert(role="file", goal="  ", parent_agent=_mock_parent()))
    assert "goal" in no_goal["error"]


def test_a_role_can_remember_its_own_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))

    assert expert_roles.model_for_role("software") is None

    expert_roles.save_role_model("software", "workbuddy/deepseek-v4.1-flash")
    assert expert_roles.model_for_role("software") == "workbuddy/deepseek-v4.1-flash"
    # 别的角色不受影响——各配各的。
    assert expert_roles.model_for_role("file") is None

    expert_roles.save_role_model("software", "")
    assert expert_roles.model_for_role("software") is None


def test_a_task_without_its_own_model_picks_up_the_role_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))
    expert_roles.save_role_model("browser", "workbuddy/deepseek-v4.1-flash")

    tasks = [{"goal": "抄一下课程页", "expert": "browser"}, {"goal": "整理文件", "expert": "file"}]

    assert _apply_expert_presets(tasks) == ""
    assert tasks[0]["model"] == "workbuddy/deepseek-v4.1-flash"
    assert "model" not in tasks[1]


def test_an_explicit_task_model_beats_the_role_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))
    expert_roles.save_role_model("browser", "workbuddy/deepseek-v4.1-flash")

    tasks = [{"goal": "抄一下课程页", "expert": "browser", "model": "zai/glm-5.3-flash"}]

    assert _apply_expert_presets(tasks) == ""
    assert tasks[0]["model"] == "zai/glm-5.3-flash"


def test_task_model_resolution_keeps_the_parent_channel_when_it_cannot_resolve():
    """解析不了那个 provider 时不许把派工打红：退回父的凭据、只换模型名。"""
    from tools.delegate_tool import _credentials_for_task_model

    fallback = {"model": None, "provider": None, "base_url": None, "api_key": "parent-key", "api_mode": None}

    assert _credentials_for_task_model(None, fallback) is fallback
    assert _credentials_for_task_model("  ", fallback) is fallback

    only_model = _credentials_for_task_model("some-model", fallback)
    assert only_model["model"] == "some-model"
    assert only_model["api_key"] == "parent-key"

    unknown = _credentials_for_task_model("no-such-provider/whatever", fallback)
    assert unknown["model"] == "whatever"
    assert unknown["api_key"] == "parent-key"


# ── 专家是 L3：独立工具 + 自己的模型（用户 11-04） ────────────────────────


def test_delegate_expert_is_its_own_tool_in_the_delegation_toolset():
    """专家不是 delegate_task 的一个参数了——它有自己的工具，工具集里看得见。"""
    import tools.delegate_tool  # noqa: F401  触发注册
    from tools.registry import registry

    entry = registry.get_entry("delegate_expert")

    assert entry is not None
    assert entry.toolset == "delegation"
    props = entry.schema["parameters"]["properties"]
    assert props["role"]["enum"] == ["file", "browser", "software", "computer"]
    assert entry.schema["parameters"]["required"] == ["role", "goal"]


def test_delegate_expert_hands_role_and_model_down(monkeypatch):
    """role / model 必须真的进到那条 task 里，否则专家又变回普通子代理。"""
    import tools.delegate_tool as dt

    seen: dict = {}

    def fake_delegate(**kwargs):
        seen.update(kwargs)
        return "{}"

    monkeypatch.setattr(dt, "delegate_task", fake_delegate)

    dt.delegate_expert(
        role="software",
        goal="跑一遍构建脚本",
        context="Windows，git-bash",
        model="workbuddy/deepseek-v4.1-flash",
        parent_agent=_mock_parent(),
    )

    task = seen["tasks"][0]
    assert task["expert"] == "software"
    assert task["goal"] == "跑一遍构建脚本"
    assert task["context"] == "Windows，git-bash"
    assert task["model"] == "workbuddy/deepseek-v4.1-flash"


def test_delegate_expert_refuses_an_unknown_role_and_an_empty_goal():
    import tools.delegate_tool as dt

    bad_role = json.loads(dt.delegate_expert(role="god", goal="x", parent_agent=_mock_parent()))
    assert "file" in bad_role["error"]

    no_goal = json.loads(dt.delegate_expert(role="file", goal="  ", parent_agent=_mock_parent()))
    assert "goal" in no_goal["error"]


def test_a_role_can_remember_its_own_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))

    assert expert_roles.model_for_role("software") is None

    expert_roles.save_role_model("software", "workbuddy/deepseek-v4.1-flash")
    assert expert_roles.model_for_role("software") == "workbuddy/deepseek-v4.1-flash"
    # 别的角色不受影响——各配各的。
    assert expert_roles.model_for_role("file") is None

    expert_roles.save_role_model("software", "")
    assert expert_roles.model_for_role("software") is None


def test_a_task_without_its_own_model_picks_up_the_role_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))
    expert_roles.save_role_model("browser", "workbuddy/deepseek-v4.1-flash")

    tasks = [{"goal": "抄一下课程页", "expert": "browser"}, {"goal": "整理文件", "expert": "file"}]

    assert _apply_expert_presets(tasks) == ""
    assert tasks[0]["model"] == "workbuddy/deepseek-v4.1-flash"
    assert "model" not in tasks[1]


def test_an_explicit_task_model_beats_the_role_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))
    expert_roles.save_role_model("browser", "workbuddy/deepseek-v4.1-flash")

    tasks = [{"goal": "抄一下课程页", "expert": "browser", "model": "zai/glm-5.3-flash"}]

    assert _apply_expert_presets(tasks) == ""
    assert tasks[0]["model"] == "zai/glm-5.3-flash"


def test_task_model_resolution_keeps_the_parent_channel_when_it_cannot_resolve():
    """解析不了那个 provider 时不许把派工打红：退回父的凭据、只换模型名。"""
    from tools.delegate_tool import _credentials_for_task_model

    fallback = {"model": None, "provider": None, "base_url": None, "api_key": "parent-key", "api_mode": None}

    assert _credentials_for_task_model(None, fallback) is fallback
    assert _credentials_for_task_model("  ", fallback) is fallback

    only_model = _credentials_for_task_model("some-model", fallback)
    assert only_model["model"] == "some-model"
    assert only_model["api_key"] == "parent-key"

    unknown = _credentials_for_task_model("no-such-provider/whatever", fallback)
    assert unknown["model"] == "whatever"
    assert unknown["api_key"] == "parent-key"
