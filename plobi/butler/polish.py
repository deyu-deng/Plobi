"""可选 L2 改写（B5）：让早报/digest 由便宜模型重写为自然语言。

「L2 便宜模型生成，L1 只转发」的后半句在这里落地：当调用方显式启用时，把
结构化模板文本交给 L2 便宜模型改写为一段简洁摘要。走 B3 的
:class:`~plobi.quota.route.QuotaAwareCompleter`（便宜优先 + aigw 兜底 +
失效 failover），因此**永远不会**动用 L1 的旗舰模型（ADR-0011 成本纪律）。

默认关闭：cron 任务是 ``no_agent``，模板是零成本基线；只有显式传入 ``send``
或真机配置了 L2 额度源时才可能产生模型调用。
"""

from __future__ import annotations

from typing import Callable, Optional

from plobi.quota.pool import QuotaPool
from plobi.quota.route import QuotaAwareCompleter
from plobi.quota.sources import QuotaSource

# 改写提示词：要求保留关键数字与待办、禁止编造（对应 R3 口径）。
POLISH_PROMPT = (
    "你是 Plobi 管家。把下面这段结构化日报改写成一段简洁、自然的中文摘要，"
    "保留所有关键数字和待办事项，不要编造内容，不要新增条目：\n\n{text}"
)


def polish(
    text: str,
    pool: Optional[QuotaPool] = None,
    *,
    send: Optional[Callable[[str, QuotaSource], Optional[str]]] = None,
    max_attempts: int = 3,
) -> Optional[str]:
    """用便宜源把 ``text`` 改写成自然语言摘要；无可用源/失败则返回 ``None``。

    ``send`` 是可注入发送函数（测试驱动 failover 时用）；缺省走
    ``QuotaAwareCompleter`` 的 OpenAI 兼容默认发送。
    """
    completer = QuotaAwareCompleter(pool=pool, send=send, max_attempts=max_attempts)
    return completer(POLISH_PROMPT.format(text=text), route=None)
