"""chatlog 这条腿的巡检：跑一轮采集 + 连续失败告警。

切片 A2（Docs/ROADMAP.md）。巡检轨道（公共触发面那一轮）最常见的
失败原因是采集进程起不来——微信退出登录、密钥不在、进程没起来。本模块把
「发现断连」从静默 warning 变成主动行为：

    那一轮采集连续失败 N 次（默认 3 次 = 30 分钟）
      → 推钉钉告警（限频：默认 1 小时最多一条）

无模型调用（ADR-0011 成本纪律）；失败计数与告警限频状态落
``PLOBI_HOME/plobi/watchdog_state.json``。恢复健康即重置计数。

**2026-11-20（`WP-WECHAT-LEG-OWNS-PROCESS`）两处形状变了**：

1. **健康判据＝那一轮采到没有**，不是「那个采集进程在不在」。按需起之后它
   采完就被收掉了，进程在不在不再说明任何事（从前它是常驻的，所以能当判据）。
2. **「自愈」那一格退役**：从前的自愈是「拉一把，让它常驻」——而**拉起来的
   那个没人收**，那正是常驻的根源；家长因此落在桌面上，桌面关着 cron／巡检
   那几轮就整条腿采不到。现在那一轮**自带起停**（`plobi/collectors/chatlog/process.py`），
   腿自己拉起来、采完收掉 ⇒ 没有「让它常驻」这回事，也就没有自愈可做。

微信 ToS 红线：本模块不改变采集节奏，tick 由 10 分钟 cron 驱动（≥30s）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .client import ChatlogClient
from .config import CollectorConfig
from .pipeline import ChatlogPipeline

logger = logging.getLogger(__name__)

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_ALERT_INTERVAL_SECONDS = 3600


def _default_state_path() -> Path:
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "watchdog_state.json"


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"consecutive_failures": 0, "last_alert_at": None}


class Watchdog:
    """One ``tick()`` = 一轮采集 + 判那一轮的结果 + (maybe) 告警。

    ``pipeline=None`` ⇒ 只探活不采集（给「名单都还没有」的那段用）。
    """

    def __init__(
        self,
        *,
        pipeline: Optional[ChatlogPipeline] = None,
        client: Optional[ChatlogClient] = None,
        config: Optional[CollectorConfig] = None,
        notifier: Optional[object] = None,
        state_path: Path | str | None = None,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        alert_interval_seconds: int = DEFAULT_ALERT_INTERVAL_SECONDS,
    ):
        self.pipeline = pipeline if pipeline is not None else ChatlogPipeline()
        self.client = client or (pipeline.client if pipeline else self.pipeline.client)
        self.config = config or (pipeline.config if pipeline else self.pipeline.config)
        self._notifier = notifier
        self.state_path = Path(state_path) if state_path else _default_state_path()
        self.failure_threshold = max(1, int(failure_threshold))
        self.alert_interval_seconds = int(alert_interval_seconds)

    # --- alerting -----------------------------------------------------------

    def _alert(self, detail: str) -> bool:
        if self._notifier is not None:
            notifier = self._notifier
        else:
            from plobi.notify import get_notifier

            notifier = get_notifier()
        if not notifier.configured:
            logger.warning("watchdog: chatlog DOWN but no notifier configured: %s", detail)
            return False
        outcome = notifier.send(
            f"[Plobi] 微信采集没采到\n连续 {self.state['consecutive_failures']} 轮没能采到。\n"
            f"详情：{detail}\n采集那一轮没能采到；若持续失败请检查微信登录态。"
        )
        return outcome.ok

    # --- state --------------------------------------------------------------

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(self.state, ensure_ascii=False)
            # Atomic write: temp file + rename prevents truncation on crash.
            tmp = self.state_path.with_suffix(".tmp")
            try:
                tmp.write_text(data, encoding="utf-8")
                tmp.replace(self.state_path)
            except BaseException:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
        except OSError:
            logger.exception("watchdog: could not persist state")

    def _now(self) -> str:
        return datetime.now().replace(microsecond=0).isoformat()

    # --- main tick ----------------------------------------------------------

    def tick(self) -> dict:
        """One tick = 那一轮采集 + 判那一轮的结果 + (maybe) 告警。"""
        return self._sweep_and_check()

    def _sweep_and_check(self) -> dict:
        self.state = _load_state(self.state_path)
        result: dict = {
            "chatlog_healthy": False,
            "consecutive_failures": 0,
            "alerted": False,
            "sweep": None,
            "detail": "",
        }

        # 1) 那一轮采集（may legitimately no-op when collector disabled）
        #    2026-11-17（`WP-COLLECT-UNIFY`）：这一趟**走公共触发面**
        #    （`sweep.run_source`），不再自己调 `pipeline.run_once` —— 那是一条**私有**
        #    触发路径，而触发面必须只有一处（落点同理，只有 `base.land_occurrences`）。
        #
        #    2026-11-20（`WP-WECHAT-LEG-OWNS-PROCESS`）：**这一轮自带起停**——腿自己
        #    把采集进程拉起来、采完收掉 ⇒ 巡检不再需要「让它常驻」这件事。
        healthy = False
        detail = ""
        if self.pipeline is not None:
            try:
                from .webhook import trigger_round

                # 窗口从配置来，不在调用处拍大数（Plan 线 09:5x 那条口径）。
                outcome = trigger_round(
                    pipeline=self.pipeline,
                    lookback_days=self.pipeline.config.time_window_days,
                )
                raw = outcome.get("report") or {}
                result["sweep"] = dict(raw.get("report") or {})
                self._notify_pending(raw.get("pending_ids") or [])
                # **健康判据＝那一轮采到没有**（不是「那个进程在不在」：按需起之后
                # 它采完就被收掉了，进程在不在不再说明任何事）。
                #
                # 两件事都要看：那一轮**整体**成不成功（进程起不来 ⇒ 整轮失败），
                # 以及那一轮**抓的时候**有没有报错（进程起来了但微信掉线 ⇒ 抓取失败
                # 了几次；从前那些失败只进一条 warning 就没了 ⇒ 「什么也没抓到」与
                # 「本来就没新消息」读起来一模一样）。
                healthy = bool(outcome.get("ok"))
                if healthy and int(result["sweep"].get("fetch_failed") or 0) > 0:
                    healthy = False
                detail = str(outcome.get("detail") or "")
            except Exception as exc:
                logger.exception("watchdog: sweep failed")
                detail = f"{type(exc).__name__}: {exc}"
        else:
            # 没有腿可采（名单都还没有）⇒ 只探活。
            try:
                healthy = bool(self.client.healthy())
            except Exception as exc:
                logger.warning("watchdog: health check raised: %s", exc)
                healthy = False

        result["detail"] = detail

        if healthy:
            result["chatlog_healthy"] = True
            result["consecutive_failures"] = 0
            self.state = {
                "consecutive_failures": 0,
                "last_alert_at": self.state.get("last_alert_at"),
            }
            self._save()
            return result

        # 2) failure bookkeeping
        self.state["consecutive_failures"] = int(self.state.get("consecutive_failures") or 0) + 1
        result["consecutive_failures"] = self.state["consecutive_failures"]

        # 3) alert: rate-limited。
        #    **不再有「自动拉起」那一格**（`WP-WECHAT-LEG-OWNS-PROCESS`）：那一轮
        #    自己会拉，拉起来的东西自己会收 ⇒ 没有「让它常驻」这回事，也就没有
        #    自愈可做；告警这句话也得跟着说实话。
        last_alert = self.state.get("last_alert_at")
        due = True
        if last_alert:
            try:
                gap = (datetime.now() - datetime.fromisoformat(last_alert)).total_seconds()
                due = gap >= self.alert_interval_seconds
            except ValueError:
                due = True
        if due and self.state["consecutive_failures"] >= self.failure_threshold:
            result["alerted"] = self._alert(detail or "微信采集那一轮没采到")
            if result["alerted"]:
                self.state["last_alert_at"] = self._now()

        self._save()
        return result

    def _notify_pending(self, pending_ids: list[str]) -> None:
        """Push what this sweep left awaiting the human (same contract as the webhook)."""
        if not pending_ids or self.pipeline is None:
            return
        try:
            from plobi.agenda.dispatch import get_dispatcher

            events = []
            for event_id in pending_ids:
                try:
                    events.append(self.pipeline.service.get(event_id))
                except Exception:
                    logger.exception("watchdog: could not load %s", event_id)
            get_dispatcher().notify_pending(events)
        except Exception:
            logger.exception("watchdog: notification dispatch failed")
