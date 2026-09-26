"""B5 管家包：早报 / 消息待办 digest / 额度预警。

三个 cron job 共享本包的数据收集（``build_*``）与消息模板（``format_*``）。
全部纯函数、零 LLM，因此 cron 任务是 ``no_agent``（零 token 成本）——L1 秘书
只转发、不进生成循环（B5 禁区）。「L2 便宜模型生成」由 :mod:`plobi.butler.polish`
提供可选改写（走 B3 额度池便宜源），默认关闭。
"""

from plobi.butler.polish import POLISH_PROMPT, polish
from plobi.butler.report import (
    build_morning,
    build_quota_alert,
    build_todo_digest,
    format_morning,
    format_quota_alert,
    format_todo_digest,
)

__all__ = [
    "POLISH_PROMPT",
    "build_morning",
    "build_quota_alert",
    "build_todo_digest",
    "format_morning",
    "format_quota_alert",
    "format_todo_digest",
    "polish",
]
