"""记忆待批的评论入账（WP-MIND-PENDING-CONTENT）。

评论是三态里的第三态：人点「接纳」／「驳回」是表态，写一句评论是**给理由**。
票面那半把评论进 ``DecisionLedger``（裁定 226）；记忆面这张纸不是票，
评论就写回**待批记录自身**的 ``payload.feedback``——原话逐字、不截断、不改票状态。

为什么不放 ``write_approval.py``：那一扇门由别线守着（多线共改，一次一家），
评论只是往已有记录的 payload 里加一个键，不必动 stage_write／list_pending 的公共形状。
写入用 os.replace 的原子替换，失败只记日志、不假装成功。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

FEEDBACK_KEY = "feedback"
_FEEDBACK_MAX = 1000  # 与原话上限同量级；超出部分不静默丢，回包里明说截了多少


def _pending_dir() -> Path:
    from plobi_constants import get_plobi_home

    return Path(get_plobi_home()) / "pending" / "memory"


def record_comment(pending_id: str, comment: str) -> bool:
    """把评论原话追加进这张纸的 ``payload.feedback``。返回是否真的落了盘。"""
    target = _pending_dir() / f"{pending_id}.json"
    if not target.is_file():
        return False
    try:
        record: Dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        payload = record.setdefault("payload", {})
        feedback = payload.setdefault(FEEDBACK_KEY, [])
        if not isinstance(feedback, list):
            feedback = []
        feedback.append({
            "comment": comment[:_FEEDBACK_MAX],
            "truncated": len(comment) > _FEEDBACK_MAX,
            "at": time.time(),
        })
        payload[FEEDBACK_KEY] = feedback
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("mind pending: comment on %s failed (%s)", pending_id, exc)
        return False


def pending_feedback() -> List[Dict[str, Any]]:
    """还没被起草过的评论，挂进读口回包等指挥那一轮读到。

    形状对齐票面 ``_pending_feedback_payload``（decision_id / comment），
    但这里叫 ``pending_id``、来源是待批记录自身——**两本账不合并**。

    去重键约定 ``<pending_id>:<序号>``：起草那一侧（复用第六枚 ``lesson_write``）把
    这个键写进 ``lesson.decision_ids``，这条评论就算起草过、不再回包。本刀不写。
    """
    out: List[Dict[str, Any]] = []
    try:
        from plobi.delegation.lesson_ledger import LessonLedger

        drafted = {d for lesson in LessonLedger().all() for d in lesson.decision_ids}
    except Exception:  # noqa: BLE001 - 账读不到就照原样给，不把评论吞掉
        drafted = set()
    try:
        for path in sorted(_pending_dir().glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            pid = str(record.get("id") or path.stem)
            payload = record.get("payload") or {}
            # 形状以 write_approval.stage_write 为准：kind 在 **payload** 里
            # （`{"action":…, "kind":…, "plan": <plan JSON 字符串>}`），记录顶层只有
            # subsystem/action/summary。早先这里按记录顶层读，永远取空。
            kind = str(payload.get("kind") or "memory")
            first_path = ""
            if kind == "mind_plan":
                try:
                    from plobi.mind import proposal as L1

                    plan = L1.Plan.from_json(payload.get("plan") or "")
                    if plan.changes:
                        first_path = plan.changes[0].logical_path
                except Exception:  # noqa: BLE001 - 一张读不出的纸不该让整包反馈消失
                    first_path = ""
            for i, fb in enumerate(payload.get(FEEDBACK_KEY) or []):
                if not isinstance(fb, dict):
                    continue
                key = f"{pid}:{i}"
                if key in drafted:
                    continue
                out.append({
                    "pending_id": pid,
                    "feedback_id": key,
                    "comment": str(fb.get("comment") or ""),
                    "kind": kind,
                    "path": first_path[len("Mind/"):] if first_path.startswith("Mind/") else first_path,
                })
    except (OSError, ValueError, TypeError) as exc:  # noqa: BLE001
        logger.debug("mind pending feedback read unavailable: %s", exc)
    return out
