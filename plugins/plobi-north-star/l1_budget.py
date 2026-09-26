"""L1 总秘书 budget hook (WP-L1-BUDGET).

P0-1: 关掉 L1 默认 profile 的回合后 skill + memory curator（"update the
       skill library" / "saving to memory"）。L1 同一条会话跑 9-14 晚上会被
       自动 curator 反扑 11-14 次 API，每次白烧 token。把
       ``agent._memory_nudge_interval`` 与 ``agent._skill_nudge_interval``
       置 0 后，``turn_finalizer.py`` 的 ``_should_review_memory / skills``
       判定直接为 False，不再进 ``_spawn_background_review``。
       **不删** ``agent/background_review.py`` —— L2 与 L3 仍走原路径。

P0-2: 把 L1 的压缩触发阈值从默认 0.50 拉到 0.35，让一条 L1 桌面会话在 200K
       ctx 模型上约 70K token 时就先压缩，不必等到 100K+ 触发「这期完了」
       的 44s timeout。**不动** ``ContextCompressor`` 默认值；运行时
       覆盖 ``agent.context_compressor.threshold_percent`` 并重算
       ``threshold_tokens``。profile/config 模板值落
       ``plugins/plobi-north-star/l1_profile_template.yaml``，便于人工
       审计与 ``tests/plobi/test_l1_budget.py`` 断言。

判定 L1：**profile name 是 ``default``** 且 **plobi_north_star 工具集启用**。
L2-agenda / L3 通常有自己的 plobi profile（不在 ``default``），自然不会被误伤。

钩子位置：``on_session_start`` —— brand-new 会话创建后第一回合开头跑一次。
agent 在 kwargs 里传入（``agent/conversation_loop.py`` 修改后提供），其它
钩子接受 ``**kwargs`` 所以加这个 kwarg 是后向兼容的。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: 默认 profile 的 L1 总秘书压缩触发阈值。
#: 0.50 → 200K ctx 上 ~100K 触发；0.35 → 200K ctx 上 ~70K 触发。
#: 不要与 ``ContextCompressor`` 默认 ``threshold_percent=0.50`` 混用——
#: 这个常量只用于 L1 default profile 的运行时覆盖。
L1_COMPRESSION_THRESHOLD: float = 0.35

#: L1 总秘书在 default profile 上对外暴露的工具集名。
#: 与 ``plugins/plobi-north-star/__init__.py::TOOLSET`` 同名。
L1_TOOLSET: str = "plobi_north_star"

#: L1 默认 profile 名。``plobi_cli.profiles.get_active_profile_name`` 在
#: ``PLOBI_HOME`` 是 ``~/.plobi`` 或 ``~/.plobi/profiles/default/`` 时
#: 都返回这个字符串。L2/L3 用自定义 profile 名，自动豁免。
L1_PROFILE_NAME: str = "default"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _as_iter(value: Any) -> Iterable[Any]:
    """把 ``enabled_toolsets`` / ``valid_tool_names`` 之类的属性规整成可迭代。"""
    if value is None:
        return ()
    if isinstance(value, (set, frozenset, list, tuple)):
        return value
    # MagicMock / SimpleNamespace 之类的代理对象：尽量取容器再迭代。
    inner = getattr(value, "__iter__", None)
    if callable(inner):
        try:
            return list(value)  # type: ignore[arg-type]
        except TypeError:
            return ()
    return ()


def _has_plobi_north_star(agent: Any) -> bool:
    """True when ``plobi_north_star`` appears in toolsets OR valid_tool_names.

    两条判定取或：前者是 ``enabled_toolsets`` 显式声明（更准），后者是
    ``valid_tool_names`` 实际暴露的工具集合（兜底——某些 spawn 路径会
    注入工具但不一定回写 ``enabled_toolsets``）。
    """
    for attr in ("enabled_toolsets", "valid_tool_names"):
        try:
            iterable = _as_iter(getattr(agent, attr, None))
        except Exception:  # pragma: no cover - 防 mock 抖动
            continue
        for entry in iterable:
            try:
                key = str(entry)
            except Exception:
                continue
            if key == L1_TOOLSET:
                return True
            # 兼容 toolsets 写成 "plobi-north-star" 或 "plobi_master" 的别名
            if key in {"plobi-north-star", "plobi_master"}:
                return True
    return False


def _profile_name(agent: Any) -> Optional[str]:
    """从 agent 上读 profile 名（属性优先），缺则回落 ``get_active_profile_name``。"""
    for attr in ("profile", "profile_name"):
        value = getattr(agent, attr, None)
        if isinstance(value, str) and value:
            return value
    try:
        from plobi_cli.profiles import get_active_profile_name

        return get_active_profile_name()
    except Exception:  # pragma: no cover - 解析失败按 None 计
        return None


def is_l1_default_profile(agent: Any) -> bool:
    """Return True iff ``agent`` 看起来是 default profile 上的 L1 总秘书。

    判定纯读 agent 属性 + ``plobi_cli.profiles.get_active_profile_name``
    回落；不引入新 import 风暴。L2-agenda / L3 通常在自定义 profile 上
    （profile name != "default"），自动豁免。
    """
    if agent is None:
        return False

    profile = _profile_name(agent)
    if profile != L1_PROFILE_NAME:
        return False

    return _has_plobi_north_star(agent)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _disable_curators(agent: Any) -> None:
    """把 L1 的 memory + skill curator nudge 间隔归零。

    对应 ``agent/turn_context.py:307-314`` 与 ``agent/turn_finalizer.py:
    480-485`` 的两段 ``_should_review_*`` 计算：间隔 <= 0 时直接跳过
    ``_spawn_background_review``。这条会话就不会在用户回合结束后再
    fork 「Review the conversation above and update the skill library /
    saving to memory」。
    """
    for attr in ("_memory_nudge_interval", "_skill_nudge_interval"):
        try:
            if getattr(agent, attr, None) is not None:
                setattr(agent, attr, 0)
        except Exception as exc:  # pragma: no cover - 防御
            logger.debug("l1_budget: failed to zero %s: %s", attr, exc)

    # 顺手把已累计的计数也清零，避免 hook 跑在 turn 1 但计数器已经满了
    # （罕见，但 ``_iters_since_skill`` 不会自动清）。
    for attr in ("_turns_since_memory", "_iters_since_skill"):
        try:
            setattr(agent, attr, 0)
        except Exception:  # pragma: no cover - 防御
            pass


def _tighten_compressor(agent: Any) -> bool:
    """把 L1 的 compressor 阈值改成 ``L1_COMPRESSION_THRESHOLD`` 并重算 threshold_tokens。

    Returns True if applied, False if the compressor isn't there yet or
    the threshold can't be safely updated (e.g. the compressor is a mock
    stub without ``_compute_threshold_tokens``).

    实现要点：
    * 不走 ``_effective_threshold_percent`` 的 75% 小窗兜底——L1 任务书 §2
      目标就是"6–8 万 token 前切开"，200K ctx 上必须用 0.35 不能再被
      拉回 0.75；这是 L1 专用覆盖，不影响其它 profile。
    * 用 ``ContextCompressor._compute_threshold_tokens`` 重算 token 阈值
      （保留小窗 85% 兜底逻辑：0.35 × 200K ≈ 70K，正常路径不走 85%），
      保证 ``should_compress`` 立刻看到新值。
    * ``_configured_threshold_percent`` 也置 0.35，``update_model`` 切换
      窗口时回退基准仍是 0.35 而不是被旧 floor 拉回 0.50。
    """
    compressor = getattr(agent, "context_compressor", None)
    if compressor is None:
        return False

    try:
        from agent.context_compressor import ContextCompressor
    except Exception:  # pragma: no cover - import 失败时跳过即可
        logger.debug("l1_budget: ContextCompressor import failed; skipping threshold tighten")
        return False

    context_length = int(getattr(compressor, "context_length", 0) or 0)
    if context_length <= 0:
        # Compressor 还没绑定模型——通常是构造顺序异常；不动它，留给后续
        # ``update_model`` 自己用 0.50 默认值。L1 真正生效在第二回合。
        return False

    max_tokens = getattr(compressor, "max_tokens", None)
    new_pct = float(L1_COMPRESSION_THRESHOLD)

    # L1 显式跳过 ``_effective_threshold_percent`` 的小窗 floor——
    # 默认 0.75 跟任务书"6–8 万 token 前切开"目标冲突。
    compressor.threshold_percent = new_pct
    # _configured_threshold_percent 是 update_model() 切换窗口时回退基准。
    # L1 在跨模型时也要保留 0.35，不能被旧 floor 拉回 0.50。
    try:
        compressor._configured_threshold_percent = new_pct
    except Exception:  # pragma: no cover - 防御
        pass

    try:
        compressor.threshold_tokens = ContextCompressor._compute_threshold_tokens(
            context_length, new_pct, max_tokens,
        )
    except Exception as exc:  # pragma: no cover - 防御
        logger.debug("l1_budget: recompute threshold_tokens failed: %s", exc)
        return False

    logger.info(
        "l1_budget: tightened L1 compressor threshold to %.2f "
        "(threshold_tokens=%d, context_length=%d, max_tokens=%s)",
        new_pct,
        int(getattr(compressor, "threshold_tokens", 0) or 0),
        context_length,
        max_tokens,
    )
    return True


def apply_l1_budget(agent: Any) -> bool:
    """Apply all L1 budget knobs to ``agent``. Returns True iff it was an L1 agent.

    Hook 调用入口。测试也可以直接调用，不必走 ``invoke_hook``。
    """
    if not is_l1_default_profile(agent):
        return False

    _disable_curators(agent)
    _tighten_compressor(agent)
    return True


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


def on_session_start(**kwargs: Any) -> None:
    """``on_session_start`` plugin hook (WP-L1-BUDGET).

    Reads ``agent`` from kwargs (set by ``agent/conversation_loop.py``)
    and applies L1 budget knobs. All other kwargs are accepted and ignored
    so the hook remains compatible with the historical session_id/model/
    platform kwargs that older callers pass.

    异常吞掉：钩子失败不应该让 ``conversation_loop`` 抛回主回合。
    """
    agent = kwargs.get("agent")
    if agent is None:
        # Backwards compat: 没传 agent 时按"无 agent"处理，直接 return。
        # production 主回路永远会传（conversation_loop 改造后）。
        return
    try:
        if apply_l1_budget(agent):
            logger.info(
                "l1_budget: applied L1 default-profile knobs (curators off, "
                "compressor threshold=%.2f)",
                L1_COMPRESSION_THRESHOLD,
            )
    except Exception as exc:  # noqa: BLE001 - 钩子必须 fail-open
        logger.warning("l1_budget: hook failed (non-fatal): %s", exc)
