"""chatlog 服务健康看门狗：巡检 + 连续失败告警 + 一次自愈尝试。

切片 A2（Docs/ROADMAP.md）。巡检轨道（pipeline.run_once）最常见的
失败原因是 chatlog 服务断了——微信退出登录、进程没起来。本模块把「发现断连」
从静默 warning 变成主动行为：

    健康检查连续失败 N 次（默认 3 次 = 30 分钟）
      → 先尝试自愈（拉起 scripts/chatlog_server.ps1，每个断连周期至多一次）
      → 仍未恢复 → 推钉钉告警（限频：默认 1 小时最多一条）

无模型调用（ADR-0011 成本纪律）；失败计数与告警限频状态落
``PLOBI_HOME/plobi/watchdog_state.json``。恢复健康即重置计数。

WP-DT-DIGEST：10 分钟 tick 是开机后最早、最稳定的心跳，所以「今天 07:30 那条
早报没发过」的补发挂在它末尾（``maybe_send_morning_catchup``）。标记文件
``PLOBI_HOME/plobi/digest_state.json`` 与 07:30 的 cron 共用——谁先发谁写，
另一边看到标记就不再发。发送失败不写标记（下个 tick 再试）。

微信 ToS 红线：本模块不改变采集节奏，tick 由 10 分钟 cron 驱动（≥30s）。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Callable, Optional

from .client import ChatlogClient
from .config import CollectorConfig
from .pipeline import ChatlogPipeline

logger = logging.getLogger(__name__)

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_ALERT_INTERVAL_SECONDS = 3600
# 07:30 是全天的分界：早报的正点由 cron 发，错过由看门狗补。
MORNING_CATCHUP_AT = dt_time(7, 30)


def _repo_root() -> Path:
    """plobi/collectors/chatlog/watchdog.py → 仓库根。无盘符硬编码。"""
    return Path(__file__).resolve().parents[3]


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
    return {"consecutive_failures": 0, "last_alert_at": None, "heal_attempted_at": None}


class Watchdog:
    """One ``tick()`` = sweep + health check + (maybe) heal/alert.

    ``heal_command`` is injectable for tests; the default launches the
    vendored chatlog server script detached. Pass ``pipeline=None`` to
    monitor health only (no collection), e.g. before the whitelist exists.
    """

    def __init__(
        self,
        *,
        pipeline: Optional[ChatlogPipeline] = None,
        client: Optional[ChatlogClient] = None,
        config: Optional[CollectorConfig] = None,
        notifier: Optional[object] = None,
        state_path: Path | str | None = None,
        heal_command: Optional[Callable[[], object]] = None,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        alert_interval_seconds: int = DEFAULT_ALERT_INTERVAL_SECONDS,
        morning_catchup: bool = True,
        digest_state_path: Path | str | None = None,
    ):
        self.pipeline = pipeline if pipeline is not None else ChatlogPipeline()
        self.client = client or (pipeline.client if pipeline else self.pipeline.client)
        self.config = config or (pipeline.config if pipeline else self.pipeline.config)
        self._notifier = notifier
        self.state_path = Path(state_path) if state_path else _default_state_path()
        self.heal_command = heal_command or self._default_heal_command
        self.failure_threshold = max(1, int(failure_threshold))
        self.alert_interval_seconds = int(alert_interval_seconds)
        self.morning_catchup = bool(morning_catchup)
        # None = derive from PLOBI_HOME (same file the 07:30 cron writes).
        self.digest_state_path = Path(digest_state_path) if digest_state_path else None

    # --- self-heal ----------------------------------------------------------

    def _default_heal_command(self):
        script = _repo_root() / "scripts" / "chatlog_server.ps1"
        if not script.exists():
            logger.warning("watchdog: heal script missing: %s", script)
            return None
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.Popen(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

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
            f"[Plobi] chatlog 服务异常\n连续 {self.state['consecutive_failures']} 次健康检查失败。\n"
            f"详情：{detail}\n已尝试自动拉起；若持续失败请检查微信登录态。"
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

    def tick(self, now: Optional[datetime] = None) -> dict:
        """One tick = sweep + health + (maybe) heal/alert + morning catch-up.

        ``now`` only feeds the morning catch-up (so tests can pin the clock);
        the health bookkeeping keeps using the wall clock.
        """
        result = self._sweep_and_check()
        if self.morning_catchup:
            try:
                result["morning_catchup"] = self.maybe_send_morning_catchup(now)
            except Exception:
                logger.exception("watchdog: morning catch-up failed")
        return result

    def _sweep_and_check(self) -> dict:
        self.state = _load_state(self.state_path)
        result: dict = {
            "chatlog_healthy": False,
            "consecutive_failures": 0,
            "healed": False,
            "alerted": False,
            "sweep": None,
        }

        # 1) collection sweep (may legitimately no-op when collector disabled)
        if self.pipeline is not None:
            try:
                report = self.pipeline.run_once()
                result["sweep"] = report.as_dict()
                self._notify_pending(report.pending_ids)
            except Exception:
                logger.exception("watchdog: sweep failed")

        # 2) health verdict
        try:
            healthy = bool(self.client.healthy())
        except Exception as exc:
            logger.warning("watchdog: health check raised: %s", exc)
            healthy = False

        if healthy:
            result["chatlog_healthy"] = True
            result["consecutive_failures"] = 0
            self.state = {
                "consecutive_failures": 0,
                "last_alert_at": self.state.get("last_alert_at"),
                "heal_attempted_at": None,
            }
            self._save()
            return result

        # 3) failure bookkeeping
        self.state["consecutive_failures"] = int(self.state.get("consecutive_failures") or 0) + 1
        result["consecutive_failures"] = self.state["consecutive_failures"]

        # 4) self-heal: once per down-episode
        if self.state["consecutive_failures"] >= self.failure_threshold and not self.state.get("heal_attempted_at"):
            try:
                outcome = self.heal_command()
                result["healed"] = outcome is not None
            except Exception as exc:
                logger.warning("watchdog: heal attempt failed: %s", exc)
            self.state["heal_attempted_at"] = self._now()

        # 5) alert: rate-limited
        last_alert = self.state.get("last_alert_at")
        due = True
        if last_alert:
            try:
                gap = (datetime.now() - datetime.fromisoformat(last_alert)).total_seconds()
                due = gap >= self.alert_interval_seconds
            except ValueError:
                due = True
        if due and self.state["consecutive_failures"] >= self.failure_threshold:
            result["alerted"] = self._alert("chatlog /api/v1/health 连续失败")
            if result["alerted"]:
                self.state["last_alert_at"] = self._now()

        self._save()
        return result

    # --- morning digest catch-up (WP-DT-DIGEST) ------------------------------

    def maybe_send_morning_catchup(self, now: Optional[datetime] = None) -> dict:
        """电脑开晚了就补发当天那条早报——同一天最多一次。

        Conditions (all must hold): local time ≥ 07:30, ``digest_state.json``
        does not already say today was delivered, and a notifier is configured.
        The body comes from the very same ``morning_body`` the 07:30 cron uses,
        so the two paths can never drift apart. A failed send leaves the marker
        untouched → the next tick (10 min later) retries; the failure only goes
        to the log, so a dead webhook cannot spam the phone.
        """
        from plobi.butler.report import (
            mark_morning_sent,
            morning_body,
            morning_sent_for,
        )

        when = now or datetime.now()
        day = when.date().isoformat()
        outcome: dict = {"due": False, "sent": False, "reason": ""}

        if when.time() < MORNING_CATCHUP_AT:
            outcome["reason"] = "before 07:30"
            return outcome

        if morning_sent_for(self.digest_state_path) == day:
            outcome["reason"] = f"already sent for {day}"
            return outcome

        outcome["due"] = True

        notifier = self._notifier
        if notifier is None:
            from plobi.notify import get_notifier

            notifier = get_notifier()
        if not notifier.configured:
            outcome["reason"] = "no notifier configured"
            logger.warning("watchdog: morning digest due but no notifier configured")
            return outcome

        service = getattr(self.pipeline, "service", None) if self.pipeline else None
        sent = notifier.send(morning_body(when, service=service))
        outcome["sent"] = bool(sent.ok)
        if sent.ok:
            mark_morning_sent(day, self.digest_state_path)
        else:
            outcome["reason"] = str(sent.detail or "send failed")
            logger.warning("watchdog: morning digest send failed: %s", sent.detail)
        return outcome

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
