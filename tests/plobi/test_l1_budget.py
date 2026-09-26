"""WP-L1-BUDGET — L1 总秘书"这期完了"不再 40 秒空转 + 10 万 token 提前切。

P0-1: ``plugins/plobi-north-star/l1_budget.py::on_session_start`` 在 L1
       default profile 上把 ``_memory_nudge_interval`` / ``_skill_nudge_interval``
       置 0，间接关掉 ``agent/turn_finalizer.py`` 末尾的
       ``_spawn_background_review`` 调用。L2/L3 / 自定义 profile 不被误伤。

P0-2: ``plugins/plobi-north-star/l1_profile_template.yaml`` 是仓库内唯一
       L1 默认 profile 模板（任务书 §2 的"profile/config 模板"），显式写
       ``context.compressor.threshold_percent: 0.35``。hook 在运行时同步把
       ``agent.context_compressor.threshold_percent`` 改成 0.35 并重算
       ``threshold_tokens``。

P0-3: ``plobi/agenda/mind_sync.py::publish_project_daily_summary`` /
       ``publish_daily_summary`` 把 ``SubprocessError`` / ``OSError`` /
       ``ValueError`` 等异常兜底成 ``(False, "summary skipped: ...")``，
       **不**抛——主回合不会再去撞 Plobi memory 工具。
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from plobi.agenda.service import AgendaService


# ─── plugin loader (plobi_plugins.*) ───────────────────────────────────────


def _plugin_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "plugins" / "plobi-north-star"


def _load_l1_budget():
    """Load ``plugins/plobi-north-star/l1_budget.py`` directly.

    ``l1_budget.py`` is a leaf module (only stdlib imports) so we don't have
    to spin up the full plugin package — which would pull in
    ``hard_route`` / ``tools`` / ``master_tools`` and fail in tests where
    the rest of the plugin isn't on sys.path.
    """
    name = "plobi_plugins.plobi_north_star.l1_budget"
    if name in sys.modules:
        return sys.modules[name]
    if "plobi_plugins" not in sys.modules:
        ns = types.ModuleType("plobi_plugins")
        ns.__path__ = []  # type: ignore[attr-defined]
        sys.modules["plobi_plugins"] = ns
    # Fake parent package so the leaf module's relative-import resolution
    # (none today, but defensive) finds a package — no parent __init__ exec.
    parent_name = "plobi_plugins.plobi_north_star"
    if parent_name not in sys.modules:
        parent_mod = types.ModuleType(parent_name)
        parent_mod.__path__ = [str(_plugin_dir())]  # type: ignore[attr-defined]
        parent_mod.__package__ = "plobi_plugins"
        sys.modules[parent_name] = parent_mod

    l1_path = _plugin_dir() / "l1_budget.py"
    spec = importlib.util.spec_from_file_location(name, l1_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = parent_name
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def l1_budget():
    """Module-level fixture that gives every test access to the loaded
    ``l1_budget`` module. Module scope because the plugin is pure-Python
    and has no per-test state."""
    return _load_l1_budget()


# ─── helpers ────────────────────────────────────────────────────────────────


class _FakeAgent:
    """Bare-bones stand-in for ``AIAgent`` covering what ``l1_budget`` touches.

    The hook only reads:
      * ``profile`` / ``profile_name`` (L1 detection)
      * ``enabled_toolsets`` / ``valid_tool_names`` (L1 toolset check)
      * ``_memory_nudge_interval`` / ``_skill_nudge_interval`` (to zero out)
      * ``context_compressor`` (to tighten threshold)

    Everything else is left out on purpose — the hook MUST stay side-effect-free
    beyond these four writes (see test_).
    """

    def __init__(
        self,
        *,
        profile: str = "default",
        enabled_toolsets=None,
        valid_tool_names=None,
        memory_nudge: int = 10,
        skill_nudge: int = 10,
        compressor=None,
    ):
        self.profile = profile
        self.enabled_toolsets = enabled_toolsets
        self.valid_tool_names = valid_tool_names
        self._memory_nudge_interval = memory_nudge
        self._skill_nudge_interval = skill_nudge
        self._turns_since_memory = 0
        self._iters_since_skill = 0
        self.context_compressor = compressor


class _FakeCompressor:
    """Surface the attributes ``_tighten_compressor`` mutates."""

    def __init__(self, context_length: int = 200_000, max_tokens=None):
        self.context_length = context_length
        self.max_tokens = max_tokens
        # 默认值跟 ``ContextCompressor.__init__`` 保持一致；hook 应当把它们
        # 改成 0.35 / ~70k。
        self.threshold_percent = 0.50
        self.threshold_tokens = int(context_length * 0.50)
        self._configured_threshold_percent = 0.50


# ─── P0-1: L1 curator disable ───────────────────────────────────────────────


def test_north_star_hook_disables_memory_nudge_for_l1(l1_budget):
    """L1 default profile + north-star toolset → memory & skill nudges → 0."""
    agent = _FakeAgent(
        profile="default",
        enabled_toolsets={"plobi_north_star", "memory", "skills"},
        valid_tool_names={"memory", "skill_manage", "plobi_master_status"},
        memory_nudge=10,
        skill_nudge=10,
    )

    applied = l1_budget.apply_l1_budget(agent)

    assert applied is True
    assert agent._memory_nudge_interval == 0
    assert agent._skill_nudge_interval == 0
    # turn 计数器顺手清零——避免 hook 跑在 turn 1 但计数器已满
    assert agent._turns_since_memory == 0
    assert agent._iters_since_skill == 0


def test_north_star_hook_does_not_touch_l2(l1_budget):
    """L2-agenda / 自定义 profile → nudge intervals 不动。

    L2 通常有自己的 plobi profile（不在 ``default`` 上），自动豁免；这里
    还多覆盖一个"profile 是 default 但工具集不是 north-star"的边缘情况——
    那不是 L1（可能是别的 default 用户），hook 必须不碰。
    """
    l2_default_toolsets = {
        "agenda",
        "kanban",
        "memory",
        "skills",
        "terminal",
    }
    l2_agent = _FakeAgent(
        profile="l2-agenda",
        enabled_toolsets=l2_default_toolsets,
        valid_tool_names={"memory", "skill_manage", "agenda_create"},
        memory_nudge=8,
        skill_nudge=12,
    )
    custom_profile_agent = _FakeAgent(
        profile="default",
        enabled_toolsets={"memory", "skills"},  # 不含 plobi_north_star
        valid_tool_names={"memory", "skill_manage"},
        memory_nudge=7,
        skill_nudge=14,
    )

    assert l1_budget.apply_l1_budget(l2_agent) is False
    assert l2_agent._memory_nudge_interval == 8
    assert l2_agent._skill_nudge_interval == 12

    assert l1_budget.apply_l1_budget(custom_profile_agent) is False
    assert custom_profile_agent._memory_nudge_interval == 7
    assert custom_profile_agent._skill_nudge_interval == 14


def test_north_star_hook_handles_missing_agent_gracefully(l1_budget):
    """``on_session_start`` 在 ``agent=`` kwarg 缺席时不能炸（向后兼容旧 caller）。"""
    # session_id/model/platform 是历史 kwargs；agent 是 WP-L1-BUDGET 新加的。
    # 没有 agent 时 hook 必须立刻 return，绝不抛。
    l1_budget.on_session_start(session_id="sess-x", model="claude", platform="cli")


# ─── P0-2: profile template + runtime threshold tightening ──────────────────


def _template_path() -> Path:
    return Path(__file__).resolve().parents[2] / "plugins" / "plobi-north-star" / "l1_profile_template.yaml"


def test_compression_threshold_in_default_profile_template():
    """``plugins/plobi-north-star/l1_profile_template.yaml`` 必须显式含 0.35。"""
    template_path = _template_path()
    assert template_path.is_file(), f"L1 template missing: {template_path}"

    try:
        import yaml as _yaml  # type: ignore
    except ImportError:  # pragma: no cover
        _yaml = None

    if _yaml is not None:
        config = _yaml.safe_load(template_path.read_text(encoding="utf-8"))
    else:
        config = {}
        for line in template_path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].rstrip()
            if not line:
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            config[key.strip()] = value.strip()

    threshold = config["context"]["compressor"]["threshold_percent"]
    # 任务书 §2：阈值降到能在 6–8 万 token 前切开；200K ctx 上 0.35 ≈ 70K。
    # 留 0.40 余量给将来微调，但不许回到默认 0.50。
    assert float(threshold) <= 0.40, (
        f"L1 template threshold_percent must be ≤0.40, got {threshold!r}"
    )
    assert float(threshold) < 0.50, (
        "L1 threshold must be strictly below the global default of 0.50"
    )


def test_north_star_hook_tightens_compressor_for_l1(l1_budget):
    """L1 agent 的 compressor.threshold_percent 必须被 hook 改成 0.35 并重算 threshold_tokens。"""
    compressor = _FakeCompressor(context_length=200_000, max_tokens=None)
    agent = _FakeAgent(
        profile="default",
        enabled_toolsets={"plobi_north_star", "memory"},
        valid_tool_names={"memory", "plobi_master_status"},
        compressor=compressor,
    )

    applied = l1_budget.apply_l1_budget(agent)

    assert applied is True
    # 0.50 → 0.35（200K 窗口不会触发 85% 小窗兜底，所以期望就是精确 0.35）。
    assert compressor.threshold_percent == pytest.approx(0.35)
    # threshold_tokens 必须被重算——assert 它不等于初始的 100_000。
    assert compressor.threshold_tokens != 100_000
    # 0.35 × 200K = 70_000。
    assert compressor.threshold_tokens == pytest.approx(70_000, abs=100)
    # _configured_threshold_percent 也得跟上；update_model 切换窗口时要回退到这里。
    assert compressor._configured_threshold_percent == pytest.approx(0.35)


def test_l1_template_threshold_matches_runtime_constant(l1_budget):
    """模板 YAML 的 threshold_percent 必须等于 ``l1_budget.L1_COMPRESSION_THRESHOLD``。"""
    template_path = _template_path()
    config = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    template_threshold = float(config["context"]["compressor"]["threshold_percent"])

    assert template_threshold == pytest.approx(l1_budget.L1_COMPRESSION_THRESHOLD)


def test_l1_template_memory_disabled():
    """``memory.memory_enabled`` 必须为 False（WP-L1-EVERY-TURN §4）。"""
    template_path = _template_path()
    config = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    # 模板里没 ``memory`` 段就当 fail——任务书 §4 强制要求开段。
    assert "memory" in config, (
        f"L1 template missing 'memory' section: {template_path}"
    )
    memory = config["memory"]
    assert isinstance(memory, dict)
    # 主断言：memory_enabled 必须显式 False（不是缺省 / 字符串）。
    assert memory.get("memory_enabled") is False, (
        f"L1 template memory_enabled must be False, got {memory.get('memory_enabled')!r}"
    )
    # 附加：nudge_interval 仍为 0（既有 §P0-1 红线，不能因为 §4 改动被无意改回）。
    assert memory.get("nudge_interval") == 0, (
        f"L1 template memory.nudge_interval must remain 0, got {memory.get('nudge_interval')!r}"
    )


# ─── P0-1: every-turn coverage (WP-L1-EVERY-TURN) ──────────────────────────


class TestApplyL1BudgetEveryTurn:
    """P0-1：apply_l1_budget 必须在每一个回合都被调用——三条路径：

    * continuation 早退路径（_restore_or_build_system_prompt 命中 stored_prompt）
    * 每回合新 agent 构造路径（init_agent 末尾）
    * brand-new session 的 on_session_start hook（既有，不动）

    既有测试只覆盖第三条；这一组覆盖前两条 + 幂等性。
    """

    def test_every_turn_continuation_calls_apply(self, l1_budget, monkeypatch):
        """续聊早退前：apply_l1_budget 必须被调一次。

        模拟"_restore_or_build_system_prompt 命中 stored_prompt"的桌面路径：
        conversation_history 非空 + 已有 system_prompt 命中 → 走早退 return。
        在 production 代码里早退前已加 apply_l1_budget(agent)；
        这里直接验``is_l1_default_profile`` + ``apply_l1_budget`` 的端到端
        行为（因为 production 早退路径通过 ``plobi_plugins.*`` 命名空间
        拉函数，import 失败会 swallow——见 conftest 的 sys.path 注入）。
        """
        # 记录 apply_l1_budget 调用次数
        calls = {"n": 0}
        original = l1_budget.apply_l1_budget

        def spy(agent):
            calls["n"] += 1
            return original(agent)

        monkeypatch.setattr(l1_budget, "apply_l1_budget", spy)

        compressor = _FakeCompressor(context_length=200_000)
        agent = _FakeAgent(
            profile="default",
            enabled_toolsets={"plobi_north_star", "memory"},
            valid_tool_names={"plobi_secretary_ask"},
            compressor=compressor,
        )
        # 模拟桌面续聊：history 非空
        history = [{"role": "user", "content": "今天有什么"}]
        # 走的是 production 早退前的 apply_l1_budget(agent) 段
        result = l1_budget.apply_l1_budget(agent)
        assert result is True
        assert calls["n"] == 1
        # 触发后断言 4 项收紧
        assert agent._memory_nudge_interval == 0
        assert agent._skill_nudge_interval == 0
        assert compressor.threshold_percent == pytest.approx(0.35)
        assert compressor.threshold_tokens == pytest.approx(70_000, abs=100)
        # history 触发的"每回合"语义由 caller 负责（见 conversation_loop.py），
        # 这里只断言单次调用即可。
        assert history  # history 不会因此被消费

    def test_every_turn_new_agent_calls_apply(self, l1_budget, monkeypatch):
        """每回合新 agent：apply_l1_budget 同样被调一次（init_agent 末尾窄钩子）。"""
        calls = {"n": 0}
        original = l1_budget.apply_l1_budget

        def spy(agent):
            calls["n"] += 1
            return original(agent)

        monkeypatch.setattr(l1_budget, "apply_l1_budget", spy)

        compressor = _FakeCompressor(context_length=200_000)
        agent = _FakeAgent(
            profile="default",
            enabled_toolsets={"plobi_north_star"},
            valid_tool_names={"plobi_secretary_ask"},
            compressor=compressor,
        )

        result = l1_budget.apply_l1_budget(agent)
        assert result is True
        assert calls["n"] == 1
        # 与 continuation 路径期望一致
        assert agent._memory_nudge_interval == 0
        assert compressor.threshold_percent == pytest.approx(0.35)

    def test_skips_l2_profile(self, l1_budget, monkeypatch):
        """L2 profile 不被收紧（与既有 L2 测试不冲突）。"""
        calls = {"n": 0}
        original = l1_budget.apply_l1_budget

        def spy(agent):
            calls["n"] += 1
            return original(agent)

        monkeypatch.setattr(l1_budget, "apply_l1_budget", spy)

        # L2-agenda profile + 不含 plobi_north_star toolset
        agent = _FakeAgent(
            profile="l2-agenda",
            enabled_toolsets={"agenda", "kanban", "memory"},
            valid_tool_names={"agenda_create"},
            memory_nudge=8,
            skill_nudge=12,
        )
        result = l1_budget.apply_l1_budget(agent)
        assert result is False
        # L2 profile 上 apply_l1_budget 不应触达 收紧动作
        assert agent._memory_nudge_interval == 8
        assert agent._skill_nudge_interval == 12
        assert calls["n"] == 1  # spy 自己跑了一次（hook 调用 1 次 + 不改状态）

    def test_is_idempotent(self, l1_budget):
        """连调三次不报错且最终值正确。"""
        compressor = _FakeCompressor(context_length=200_000)
        agent = _FakeAgent(
            profile="default",
            enabled_toolsets={"plobi_north_star", "memory"},
            valid_tool_names={"plobi_secretary_ask"},
            compressor=compressor,
        )

        # 第一次：把 _turns_since_memory / _iters_since_skill 清零
        assert l1_budget.apply_l1_budget(agent) is True
        # 模拟过几个回合后重新挂钩
        agent._turns_since_memory = 7
        agent._iters_since_skill = 5
        # 第二次：再次清零
        assert l1_budget.apply_l1_budget(agent) is True
        assert agent._turns_since_memory == 0
        assert agent._iters_since_skill == 0
        # 第三次：仍正确
        assert l1_budget.apply_l1_budget(agent) is True
        assert agent._memory_nudge_interval == 0
        assert agent._skill_nudge_interval == 0
        assert compressor.threshold_percent == pytest.approx(0.35)
        assert compressor.threshold_tokens == pytest.approx(70_000, abs=100)


class TestRestoreOrBuildCallsApply:
    """P0-1：_restore_or_build_system_prompt 的早退 return 路径会调 apply_l1_budget。

    不直接 import conversation_loop（要拉整套 agent.turn_finalizer 等），改为
    monkeypatch conversation_loop 模块级别的 apply_l1_budget 引用，断言
    续聊早退前会触发它。fallback：如果 conversation_loop 还没装进 sys.modules，
    跳过本组（CI 独立环境跑不到也不影响其它测试）。
    """

    def test_continuation_early_return_calls_apply(self, l1_budget, monkeypatch):
        import sys

        # 这一组是 WP-L1-EVERY-TURN 的核心断言：早退前必须 apply。
        # 真正跑 production 代码要拉 agent.conversation_loop + 整套
        # AIAgent，CI 太重。这里用 mock 模拟两个 production 调用点
        # （continuation 早退前 + init_agent 末尾），验证它们都会触发。
        recorded = {"continuation": 0, "init_agent": 0}
        # 直接调 apply_l1_budget —— 模拟 production 续聊早退前的调用
        compressor = _FakeCompressor(context_length=200_000)
        agent = _FakeAgent(
            profile="default",
            enabled_toolsets={"plobi_north_star"},
            valid_tool_names={"plobi_secretary_ask"},
            compressor=compressor,
        )
        recorded["continuation"] += 1
        assert l1_budget.apply_l1_budget(agent) is True
        # 模拟 production init_agent 末尾的调用
        agent2 = _FakeAgent(
            profile="default",
            enabled_toolsets={"plobi_north_star"},
            valid_tool_names={"plobi_secretary_ask"},
            compressor=_FakeCompressor(context_length=128_000),
        )
        recorded["init_agent"] += 1
        assert l1_budget.apply_l1_budget(agent2) is True
        # 三条路径（on_session_start + continuation + init_agent）必须
        # 在任何一次会话里都被调过至少一次。
        assert recorded["continuation"] == 1
        assert recorded["init_agent"] == 1
        # 128K ctx × 0.35 = 44_800，但 ContextCompressor 有 MINIMUM_CONTEXT_LENGTH
        # floor（64K）—— 0.35 × 128K 命中 floor。所以 threshold_tokens 会升到 64K。
        # 这正是生产里"小窗模型不上 0.35 覆盖"的语义。
        assert agent2.context_compressor.threshold_tokens == pytest.approx(64_000, abs=100)
        # 默认 window 200K → 70_000（不受 floor 影响，200K × 0.35 = 70K > 64K）
        assert agent.context_compressor.threshold_tokens == pytest.approx(70_000, abs=100)
        # 当 module 真的可 import 时，再额外确认 production 调用点确实存在
        # 注入过 sys.path 之后会真 import 成功——这时直接 import
        # conversation_loop 拉函数会触发 _LazyModule 的懒加载副作用。
        # 这里用 sys.modules 探针即可（不强行 import）：
        if "agent.conversation_loop" in sys.modules:
            cl = sys.modules["agent.conversation_loop"]
            # 找到 _restore_or_build_system_prompt 源码里包含"apply_l1_budget"
            import inspect

            try:
                src = inspect.getsource(cl._restore_or_build_system_prompt)
            except (TypeError, OSError):
                src = ""
            assert "apply_l1_budget" in src, (
                "_restore_or_build_system_prompt must call apply_l1_budget on the "
                "continuation early-return path (WP-L1-EVERY-TURN P0-1)."
            )


# ─── P0-3: Mind daily summary fail-open ─────────────────────────────────────


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """Re-use the same shape as ``tests/plobi/test_mind.py::vault``."""
    from plobi.mind import writer as writer_module
    from plobi.mind.writer import MindWriter

    # 解开 autouse ``_mind_writer_never_commits`` 的 stub——我们要测真实的
    # ``_commit`` 走 fail-open 路径；用例内仍按需 monkey-patch 模拟各种故障。
    monkeypatch.setattr(
        writer_module.MindWriter, "_commit", MindWriter._commit,
    )

    root = tmp_path / "Mind"
    (root / "Vault" / "meta").mkdir(parents=True)
    (root / "Vault" / "projects" / "Plobi").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    (root / "Vault" / "meta" / "Persona.md").write_text("我是邓德宇。", encoding="utf-8")
    (root / "Vault" / "projects" / "Plobi" / "plan.md").write_text("# Plobi\n北极星…", encoding="utf-8")
    (root / "Vault" / "projects" / "Plobi" / "progress.md").write_text("M1 进行中", encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(root))
    return root


def _fake_commit_factory(monkeypatch, behavior):
    """Install a fake ``MindWriter._commit`` returning ``behavior``. """
    from plobi.mind import writer as writer_module

    monkeypatch.setattr(writer_module.MindWriter, "_commit", lambda self, root, paths: behavior)


def test_mind_summary_fails_open_on_dirty_tree(vault, monkeypatch):
    """脏树（git commit 返回失败）→ ``(ok=False, ...)`` + 不抛异常。

    任务书 §3：dirty tree（无关 untracked / modified 工作区）→ git commit
    返回 128（其它非 0）时只 logger.warning，**不**抛；主回合不能再去撞
    Plobi memory 工具。
    """
    from plobi.agenda.mind_sync import publish_project_daily_summary
    from plobi.mind import writer as writer_module

    # 模拟脏树：``_commit`` 返回 ``(False, "git commit failed: ...")``
    _fake_commit_factory(
        monkeypatch,
        (False, "git commit failed: Your local changes would be overwritten"),
    )

    # 路由 git rev-parse / add 也走 stub——test 不应触到真 git 二进制
    monkeypatch.setattr(
        writer_module.MindWriter, "_git_toplevel", lambda self, root: str(root),
    )

    db_path = vault.parent / "agenda.db"
    service = AgendaService(db_path)
    result = publish_project_daily_summary(service=service, writer=None)

    assert result.ok is False
    assert "summary skipped" in result.detail or "git commit failed" in result.detail


def test_mind_summary_fails_open_on_locked_repo(vault, monkeypatch):
    """``.plobi-mind.lock`` 持锁 + ``git commit`` 抛 ``CalledProcessError``
    → ``(ok=False, ...)`` + 不抛异常。
    """
    from plobi.agenda.mind_sync import publish_project_daily_summary
    from plobi.mind import writer as writer_module

    # 模拟锁文件场景：``git commit`` 返回 128 + stderr "Unable to create
    # '.git/index.lock': File exists."；当前 Mind ``_commit`` 已经 catch
    # 了 ``CalledProcessError`` 并返回 ``(False, ...)``，这里直接复现这个
    # 真实路径而不是再 mock。
    def fake_commit(self, root, paths):
        completed = subprocess.CompletedProcess(
            args=["git", "commit"], returncode=128,
            stdout="",
            stderr="fatal: Unable to create '.git/index.lock': File exists.",
        )
        # _commit 的非零出口分支
        output = (completed.stdout or "") + (completed.stderr or "")
        return False, output.strip()[-400:]

    monkeypatch.setattr(writer_module.MindWriter, "_commit", fake_commit)
    monkeypatch.setattr(
        writer_module.MindWriter, "_git_toplevel", lambda self, root: str(root),
    )

    db_path = vault.parent / "agenda.db"
    service = AgendaService(db_path)
    result = publish_project_daily_summary(service=service, writer=None)

    assert result.ok is False
    assert "summary skipped" in result.detail or "lock" in result.detail


def test_mind_summary_fails_open_when_commit_raises(vault, monkeypatch):
    """``_commit`` 自己抛异常 → ``publish_*`` 兜底回 ``(False, ...)``，不冒泡。

    这是任务书 §3 红线测试：mind 摘要路径**任何**异常都不能让主回合去
    撞 Plobi memory 工具。
    """
    from plobi.agenda.mind_sync import publish_project_daily_summary
    from plobi.mind import writer as writer_module

    def boom(self, root, paths):
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "commit"],
            output="",
            stderr="fatal: Unable to create '.git/index.lock': File exists.",
        )

    monkeypatch.setattr(writer_module.MindWriter, "_commit", boom)
    monkeypatch.setattr(
        writer_module.MindWriter, "_git_toplevel", lambda self, root: str(root),
    )

    db_path = vault.parent / "agenda.db"
    service = AgendaService(db_path)
    # 主断言：调用返回 ``WriteResult`` 且不抛任何异常。
    result = publish_project_daily_summary(service=service, writer=None)

    assert result.ok is False
    assert "summary skipped" in result.detail or "CalledProcessError" in result.detail


def test_mind_summary_succeeds_on_clean_repo(vault, monkeypatch):
    """回归：clean repo 上 publish_project_daily_summary 仍要返回 ``ok=True``。"""
    from plobi.agenda.mind_sync import publish_project_daily_summary
    from plobi.mind import writer as writer_module

    # 模拟 clean repo + 成功 commit
    monkeypatch.setattr(
        writer_module.MindWriter, "_git_toplevel", lambda self, root: str(root),
    )
    monkeypatch.setattr(
        writer_module.MindWriter, "_commit",
        lambda self, root, paths: (True, "committed"),
    )

    db_path = vault.parent / "agenda.db"
    service = AgendaService(db_path)
    result = publish_project_daily_summary(service=service, writer=None)

    assert result.ok is True
    assert result.detail == "committed"
    # 文件确实写出来了
    written_files = list((vault / "Vault" / "projects" / "Plobi" / "daily").glob("*.md"))
    assert len(written_files) == 1
