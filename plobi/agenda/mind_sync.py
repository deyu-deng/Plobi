"""Publish the day's agenda into Mind.

The coordinator between two modules that must not know each other: agenda
renders the digest, Mind persists it.

Two targets (MIND_ADAPTER_PLAN.md 写接缝）：

- ``Loom/raw/chat-logs/digested/<date>/agenda.md`` — daily AI-authored log
  (the historical channel, kept for chat-log digests);
- ``Vault/projects/Plobi/daily/<date>.md`` — the current-project daily
  snapshot inside the official brain's Plobi subtree (user-approved write
  zone; the lowercase ``plobi`` archive stays read-only).

Both funnel through the same serialized, model-free :class:`MindWriter`
service. Runtime state remains SQLite-only (ADR-0007) — these files are
day-scoped read-only snapshots for the second brain, never the source of
truth. Failures return ``ok=False`` and never block the conversation.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from typing import Optional

from plobi.mind import AI_WRITE_ROOT, MindWriter, WriteResult, get_writer
from plobi.mind.paths import is_safe_relative

from .service import AgendaService, get_service

logger = logging.getLogger(__name__)

PROJECT_DAILY_PREFIX = "Vault/projects/Plobi/daily"

#: 任务书 WP-L1-BUDGET §3 红线：mind 摘要路径抛任何异常都会让主回合去撞
#: Plobi memory 工具补课——所以这里在 ``publish_*`` 里把 SubprocessError /
#: OSError / ValueError / 一般 Exception 都吞掉，统一回 ``(False,
#: "summary skipped: ...")``。Mind ``_commit`` 自身已经 catch 了
#: ``(OSError, subprocess.SubprocessError)`` 并返回 ``(False, ...)``；这里
#: 兜底的是 agenda 渲染、写锁超时、``safe_target`` 抛 ``UnsafeMindPath``
#: 等 ``MindWriter.write`` 之前的边界情况。
_FALLBACK_EXC: tuple[type[BaseException], ...] = (
    subprocess.SubprocessError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    RuntimeError,
)


def summary_relative_path(day: Optional[datetime] = None) -> str:
    target = (day or datetime.now()).date().isoformat()
    return f"{AI_WRITE_ROOT}/{target}/agenda.md"


def project_daily_relative_path(day: Optional[datetime] = None) -> str:
    """当日项目快照路径（kebab/date-safe：ISO 日期本身就是 kebab）。"""
    target = (day or datetime.now()).date().isoformat()
    return f"{PROJECT_DAILY_PREFIX}/{target}.md"


def publish_daily_summary(
    *,
    service: Optional[AgendaService] = None,
    writer: Optional[MindWriter] = None,
    day: Optional[datetime] = None,
) -> WriteResult:
    """Write one day's digest. Safe to re-run — it overwrites that day's file.

    WP-L1-BUDGET §3 fail-open：摘要失败（含 ``.plobi-mind.lock`` 持锁、
    脏树、untracked、``safe_target`` 抛 ``UnsafeMindPath`` 等任意
    ``_FALLBACK_EXC`` 命中）一律返回 ``(False, "summary skipped: ..."))``
    而**不**抛异常——主回合不该再去撞 Plobi memory 工具补课。
    """
    rel = summary_relative_path(day)
    try:
        agenda = service or get_service()
        sink = writer or get_writer()

        if not sink.available:
            logger.info("mind: vault not configured; skipping agenda digest")
            return WriteResult(ok=False, written=[], skipped=[rel], detail="mind unavailable")

        return sink.write_one(rel, agenda.daily_summary(day))
    except _FALLBACK_EXC as exc:
        logger.warning(
            "mind: agenda digest skipped: %s (%s: %s)",
            rel,
            type(exc).__name__,
            exc,
        )
        return WriteResult(ok=False, written=[], skipped=[rel], detail=f"summary skipped: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - 红线：最后兜底，绝不抛
        logger.warning(
            "mind: agenda digest skipped (unexpected): %s (%s: %s)",
            rel,
            type(exc).__name__,
            exc,
        )
        return WriteResult(ok=False, written=[], skipped=[rel], detail=f"summary skipped: {type(exc).__name__}: {exc}")


def publish_project_daily_summary(
    *,
    service: Optional[AgendaService] = None,
    writer: Optional[MindWriter] = None,
    day: Optional[datetime] = None,
) -> WriteResult:
    """当日日程摘要 → ``Vault/projects/Plobi/daily/<date>.md``（WP-MIND 写接缝）。

    - 无模型：内容是 ``AgendaService.daily_summary`` 的确定性渲染，真源
      仍是 agenda.db（SQLite，ADR-0007），Mind 里只是当日快照；
    - 串行：走 MindWriter 服务（锁 + 白名单 + verifier/commit），不并行写；
    - 幂等：同一天重跑覆盖同文件；
    - 失败：返回 ``ok=False``，调用方降级为日志告警，绝不抛异常阻塞主对话。

    WP-L1-BUDGET §3 fail-open：所有可能的异常路径都被 ``_FALLBACK_EXC``
    + 最外层 ``Exception`` 双重兜底转成 ``(False, "summary skipped: ...")``，
    绝不向上冒泡。
    """
    rel = project_daily_relative_path(day)
    try:
        if not is_safe_relative(rel):  # 防御式断言；Plobi 大写目录在 SAFE_PREFIXES
            logger.warning("mind: refused project daily summary path %s", rel)
            return WriteResult(ok=False, written=[], skipped=[rel], detail="unsafe path")

        agenda = service or get_service()
        sink = writer or get_writer()
        if not sink.available:
            logger.info("mind: vault not configured; skipping project daily summary")
            return WriteResult(ok=False, written=[], skipped=[rel], detail="mind unavailable")

        result = sink.write_one(rel, agenda.daily_summary(day))
        if not result.ok:
            logger.warning("mind: project daily summary not written: %s", result.detail)
        return result
    except _FALLBACK_EXC as exc:
        logger.warning(
            "mind: project daily summary skipped: %s (%s: %s)",
            rel,
            type(exc).__name__,
            exc,
        )
        return WriteResult(ok=False, written=[], skipped=[rel], detail=f"summary skipped: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - 红线：最后兜底，绝不抛
        logger.warning(
            "mind: project daily summary skipped (unexpected): %s (%s: %s)",
            rel,
            type(exc).__name__,
            exc,
        )
        return WriteResult(ok=False, written=[], skipped=[rel], detail=f"summary skipped: {type(exc).__name__}: {exc}")
