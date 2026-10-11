"""B5 管家包任务注册的薄壳（真源在 ``plobi/butler/nightly.py``）。

留这个名字只为两个既有调用方：``scripts/plobi/register_cron.py --all`` 与任何按旧路径
import 它的脚本。作业表 / schedule / 脚本复制以前住在这里，而产品启动路径（桌面后端
``_lifespan``）也要用同一张表——脚本目录不是包，产品码不该反过来 import 这里，
所以表搬进了 ``plobi/``，这里只剩转发（裁定 100「一处真源」）。
"""

from __future__ import annotations

from plobi.butler.nightly import BUTLER_JOBS, register_butler_jobs  # noqa: F401

__all__ = ["BUTLER_JOBS", "register_butler_jobs"]
