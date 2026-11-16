"""记忆待批的评论入账（WP-MIND-PENDING-CONTENT）。

评论是三态里的第三态：人点「接纳」／「驳回」是表态，写一句评论是**给理由**。
票面那半把评论进 ``DecisionLedger``（裁定 226）；记忆面这张纸不是票，
评论写两处——**纸上**（``payload.feedback``，谁在读这张纸时能看见）与
**独立的收发篮**（``_feedback.jsonl``，纸没了也还在）。

为什么必须另有一个篮：``_approve`` 与 ``_reject`` 都会 ``discard_pending`` 删掉纸
（`plobi_cli/write_approval_commands.py:129`、`:246`），而「点一句评论、顺手点头」
正是最自然的动作——评论若只挂在纸上，最该被学的那一刻恰好被删掉。
所以评论在**写下的那一刻**就落篮，删纸不影响它。

为什么不放 ``write_approval.py``：那一扇门由别线守着（多线共改，一次一家），
评论只是往已有记录的 payload 里加一个键，不必动 stage_write／list_pending 的公共形状。
写入用 os.replace 的原子替换、篮用单行 append，失败只记日志、不假装成功。
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
_INBOX_NAME = "_feedback.jsonl"  # 不以 .json 结尾，不会被 list_pending 当成一张待批纸
_INBOX_READ_MAX = 200  # 读口最多带这么多条；再老的留在篮里，不删


def _pending_dir() -> Path:
    from plobi_constants import get_plobi_home

    return Path(get_plobi_home()) / "pending" / "memory"


def _inbox() -> Path:
    return _pending_dir() / _INBOX_NAME


def _first_path(payload: Dict[str, Any], kind: str) -> str:
    """这张纸指向的第一个对象（去 `Mind/` 逻辑前缀）。读不出来就空串。

    kind 与正文都在 payload 里（`{"action":…, "kind":…, "plan": <plan JSON>}`，
    见 `write_approval.stage_write`）；记录顶层只有 id/subsystem/action/summary。
    """
    if kind != "mind_plan":
        return ""
    try:
        from plobi.mind import proposal as L1

        plan = L1.Plan.from_json(payload.get("plan") or "")
        if not plan.changes:
            return ""
        logical = plan.changes[0].logical_path
        return logical[len("Mind/"):] if logical.startswith("Mind/") else logical
    except Exception:  # noqa: BLE001 - 一张读不出的纸不该让整包反馈消失
        return ""


def record_comment(pending_id: str, comment: str) -> bool:
    """把评论原话记进这张纸与收发篮。返回是否真的落了盘（两处都没成＝False）。"""
    target = _pending_dir() / f"{pending_id}.json"
    if not target.is_file():
        return False
    now = time.time()
    entry = {
        "comment": comment[:_FEEDBACK_MAX],
        "truncated": len(comment) > _FEEDBACK_MAX,
        "at": now,
    }
    on_paper = False
    obj_path = ""
    try:
        record: Dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        payload = record.setdefault("payload", {})
        feedback = payload.setdefault(FEEDBACK_KEY, [])
        if not isinstance(feedback, list):
            feedback = []
        entry["feedback_id"] = f"{pending_id}:{len(feedback)}"
        entry["kind"] = str(payload.get("kind") or "memory")
        obj_path = _first_path(payload, str(entry["kind"]))
        feedback.append({"comment": entry["comment"], "truncated": entry["truncated"], "at": now})
        payload[FEEDBACK_KEY] = feedback
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        on_paper = True
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("mind pending: comment on %s failed (%s)", pending_id, exc)

    # 独立篮：纸被批掉／驳回之后，这条评论仍然要能被指挥读到。写不进去只记日志。
    line = {
        "pending_id": pending_id,
        "feedback_id": entry.get("feedback_id") or f"{pending_id}:?",
        "kind": entry.get("kind") or "memory",
        "path": obj_path,
        "comment": entry["comment"],
        "truncated": entry["truncated"],
        "at": now,
    }
    try:
        _pending_dir().mkdir(parents=True, exist_ok=True)
        with open(_inbox(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.warning("mind pending: feedback inbox append failed (%s)", exc)
        return on_paper


def _from_inbox() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    path = _inbox()
    if not path.is_file():
        return out
    try:
        for raw in path.read_text(encoding="utf-8").splitlines()[-(_INBOX_READ_MAX * 2):]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue  # 半行（进程被杀）不该让整篮读不出来
            if isinstance(row, dict) and row.get("feedback_id"):
                out.append(row)
    except OSError as exc:
        logger.debug("mind pending feedback inbox unavailable: %s", exc)
    return out[-_INBOX_READ_MAX:]


def pending_feedback() -> List[Dict[str, Any]]:
    """还没被起草过的评论，挂进读口回包等指挥那一轮读到。

    形状对齐票面 ``_pending_feedback_payload``（decision_id / comment），
    但这里叫 ``pending_id``、来源是待批记录自身——**两本账不合并**。

    去重键约定 ``<pending_id>:<序号>``：起草那一侧（复用第六枚 ``lesson_write``）把
    这个键写进 ``lesson.decision_ids``，这条评论就算起草过、不再回包。本刀不写。

    来源两处合成一份：**篮**（写下即落，纸删了还在）＋仍在的纸上还挂着的评论
    （本轮之前写下的那几条只落在纸上）。同一 ``feedback_id`` 只出一次。
    """
    try:
        from plobi.delegation.lesson_ledger import LessonLedger

        drafted = {d for lesson in LessonLedger().all() for d in lesson.decision_ids}
    except Exception:  # noqa: BLE001 - 账读不到就照原样给，不把评论吞掉
        drafted = set()

    merged: Dict[str, Dict[str, Any]] = {}
    for row in _from_inbox():
        merged[str(row["feedback_id"])] = {
            "pending_id": str(row.get("pending_id") or ""),
            "feedback_id": str(row["feedback_id"]),
            "comment": str(row.get("comment") or ""),
            "kind": str(row.get("kind") or "memory"),
            "path": str(row.get("path") or ""),
        }

    # 纸质来源：补上篮里还没有的（这张纸的 kind 在 payload 里，正文在 payload["plan"]）
    try:
        for path in sorted(_pending_dir().glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            pid = str(record.get("id") or path.stem)
            payload = record.get("payload") or {}
            kind = str(payload.get("kind") or "memory")
            first_path = _first_path(payload, kind)
            for i, fb in enumerate(payload.get(FEEDBACK_KEY) or []):
                if not isinstance(fb, dict):
                    continue
                key = f"{pid}:{i}"
                if key in merged:
                    continue
                merged[key] = {
                    "pending_id": pid,
                    "feedback_id": key,
                    "comment": str(fb.get("comment") or ""),
                    "kind": kind,
                    "path": first_path,
                }
    except (OSError, ValueError, TypeError) as exc:  # noqa: BLE001
        logger.debug("mind pending feedback read unavailable: %s", exc)

    return [row for key, row in merged.items() if key not in drafted]
