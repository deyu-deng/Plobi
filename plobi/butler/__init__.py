"""B5 管家包：早报 / 消息待办 digest / 额度预警。

本包只提供**数据收集**（``build_*``）与**消息模板**（``format_*``），被
``scripts/plobi/cron_*.py`` 那几个 ``no_agent`` 脚本调用。全部纯函数、零 LLM，
因此那些作业是零 token 成本——L1 秘书只转发、不进生成循环（B5 禁区）。

**作业本身归通用 Cron**（桌面「定时任务」页 / ``/api/cron/jobs`` / ``plobi cron create``）：
谁要早报、几点跑，由用户在那里建、在那里改。代码不再声明作业表、不再在启动时播种、
也不再回刷别人改过的作业行（WP-BUTLER-SEED-REMOVAL，2026-10-21 剔掉那层私有播种；
原 ``plobi/butler/nightly.py`` 已删，别照旧路径找）。

「L2 便宜模型生成」由 :mod:`plobi.butler.polish` 提供可选改写（走 B3 额度池便宜源），
默认关闭。
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
