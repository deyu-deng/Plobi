"""后台档案维护 review —— 每隔 N 轮 fork 一个 agent 更新自己那份 dossier。

与 ``agent/background_review.py``（memory/skill 萃取）并列、互不相交：那一族
由 `WP-MEMORY-SURGICAL-EDITS` 拥有，本模块由 `WP-DOSSIER-AUTO-MAINTAIN` 拥有。

写侧真源是 ``tools/human_dossier_tools.dossier_write`` 的**字段级**原子工具
（只改点名的 goal/status/stages/log_append，其余键原样留着）——本模块不新增
任何写盘路径，只在后台把一个 fork 的注意力引到「这一轮有没有改变该改档案」。

注入侧对应 ``plobi/mind/provider.py`` 的 prefetch 摘要：那里读同一份
``Dossiers/<category>/<name>/dossier.json`` 给模型看，这里写回它。屏上那张卡、
上下文摘要、本模块写回的是同一份文件（P6：每一条能力都能指到 code）。

安全边界（与 memory/skill review 一致）：
  * fork 继承父的实时 runtime（provider/model/凭据），默认是主模型；
  * 线程工具白名单**只放 dossier 那五把**，其余一律 deny；
  * fork 关掉持久化 / 压缩 / 二次 nudge，绝不写用户的真实会话行；
  * 输出静音，只在成功写回后给一条简短摘要。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List

from agent.thread_scoped_output import thread_scoped_silence

logger = logging.getLogger(__name__)

DOSSIER_REVIEW_PROMPT = (
    "Review the conversation above for changes to the project this agent owns.\n\n"
    "Read your own dossier first with `dossier_read` — it lives at "
    "`Dossiers/<category>/<name>/dossier.json`, the one named in your identity "
    "block (your `mind_subtree`). If you cannot tell which dossier is yours, "
    "stop and say so; never guess or touch another agent's dossier.\n\n"
    "Then update ONLY what this conversation actually changed, using "
    "`dossier_write` with just the fields that moved:\n"
    "  - `goal` — only if the user restated or changed what this project is for.\n"
    "  - `status` — one short paragraph on the true current state, if it moved.\n"
    "  - `stages` — mark a stage `done` only when it is really finished; add a "
    "stage only for a newly committed piece of work, not a passing idea.\n"
    "  - `log_append` — one line of running history only when something "
    "durable happened.\n\n"
    "Never overwrite `tasks.json` from here (that checklist is the user's), "
    "never write another category/name, never invent progress. Name only the "
    "changed fields; `dossier_write` leaves every other key byte-identical. "
    "If nothing about the project's goal, state, or stages actually changed, "
    "say 'Nothing to save.' and stop."
)

# dossier 工具集那五把（`toolsets.py` 的 "dossier" 定义）
_DOSSIER_TOOLS = (
    "dossier_read",
    "dossier_write",
    "dossier_list",
    "dossier_task_toggle",
    "dossier_stage_update",
)


def _fork_runtime(agent: Any) -> Dict[str, Any]:
    """沿用 background review 的 runtime 继承口径：父的实时 provider/凭据。

    不 import ``agent.background_review._resolve_review_runtime``——那是别人认领
    的模块，本刀整档不碰。默认（auto）就用父模型，与父共享 prompt cache。
    """
    try:
        rt = agent._current_main_runtime()
    except Exception:
        rt = {}
    api_mode = rt.get("api_mode") or None
    if api_mode == "codex_app_server":
        api_mode = "codex_responses"
    return {
        "provider": agent.provider,
        "model": agent.model,
        "api_key": rt.get("api_key") or None,
        "base_url": rt.get("base_url") or None,
        "api_mode": api_mode,
        "credential_pool": getattr(agent, "_credential_pool", None),
        "request_overrides": dict(getattr(agent, "request_overrides", {}) or {}),
    }


def _run_dossier_review(agent: Any, messages_snapshot: List[Dict]) -> None:
    """线程体：fork 一个只拿 dossier 工具的 agent，跑一次档案维护。"""
    from run_agent import AIAgent
    from tools.terminal_tool import set_approval_callback as _set_approval
    from plobi_cli.plugins import set_thread_tool_whitelist, clear_thread_tool_whitelist

    def _deny(command, description, **kwargs):
        logger.warning("Dossier review auto-denied command: %s (%s)", command, description)
        return "deny"

    try:
        _set_approval(_deny)
    except Exception:
        pass

    rt = _fork_runtime(agent)
    kwargs: Dict[str, Any] = {}
    if isinstance(rt.get("base_url"), str) and rt["base_url"]:
        kwargs["base_url"] = rt["base_url"]
    if isinstance(rt.get("api_key"), str) and rt["api_key"]:
        kwargs["api_key"] = rt["api_key"]

    set_thread_tool_whitelist(
        set(_DOSSIER_TOOLS),
        deny_msg_fmt="Dossier review denied non-whitelisted tool: {tool_name}.",
    )
    fork = None
    try:
        with thread_scoped_silence():
            fork = AIAgent(
                model=rt.get("model") or agent.model,
                provider=rt.get("provider") or agent.provider,
                api_mode=rt.get("api_mode"),
                max_iterations=12,
                quiet_mode=True,
                platform=agent.platform,
                parent_session_id=agent.session_id,
                enabled_toolsets=getattr(agent, "enabled_toolsets", None),
                disabled_toolsets=getattr(agent, "disabled_toolsets", None),
                credential_pool=rt.get("credential_pool"),
                request_overrides=rt.get("request_overrides") or {},
                skip_memory=True,
                **kwargs,
            )
            # 隔离：不写用户会话行、不压缩、不触发二次 review。
            fork._persist_disabled = True
            fork._session_db = None
            fork._session_json_enabled = False
            fork._end_session_on_close = False
            fork.compression_enabled = False
            fork._memory_nudge_interval = 0
            fork._skill_nudge_interval = 0
            if hasattr(fork, "_dossier_nudge_interval"):
                fork._dossier_nudge_interval = 0
            fork.suppress_status_output = True
            fork.session_id = agent.session_id

            # 与 memory/skill review 同一形状：对话快照走 history，prompt 作
            # 最后一条用户消息；不做 digest（默认同模型、走热缓存）。
            fork.run_conversation(
                user_message=(
                    DOSSIER_REVIEW_PROMPT
                    + "\n\nYou can only call dossier tools. Other tools will be "
                    "denied at runtime — do not attempt them."
                ),
                conversation_history=messages_snapshot,
            )
    except Exception as exc:  # best-effort，绝不影响主对话
        logger.warning("Background dossier review failed (non-fatal): %s", exc)
    finally:
        clear_thread_tool_whitelist()
        if fork is not None:
            with thread_scoped_silence():
                try:
                    fork.shutdown_memory_provider()
                except Exception:
                    pass
                try:
                    fork.close()
                except Exception:
                    pass
        try:
            _set_approval(None)
        except Exception:
            pass


def spawn_dossier_review(agent: Any, messages_snapshot: List[Dict]) -> threading.Thread:
    """起一个后台 daemon 线程做档案维护；返回线程对象便于测试。"""
    from tools.thread_context import propagate_context_to_thread

    def _target() -> None:
        _run_dossier_review(agent, messages_snapshot)

    t = threading.Thread(
        target=propagate_context_to_thread(_target), daemon=True, name="bg-dossier-review"
    )
    t.start()
    return t


__all__ = ["DOSSIER_REVIEW_PROMPT", "spawn_dossier_review"]
