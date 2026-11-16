"""会移动的授权门槛：一条 lesson 从起草到生效的全生命周期（裁定 226）。

用户的三按钮表态和对话里那句「以后这类你定」，最终都落成这里的一条 lesson：
**Agent 起草（proposed），人点头才生效（approved），一句话就能撤回（revoked）**。
这与裁定 199 的旧计数不是同一件事——计数只决定「可以起草了吗」，放行只认
`approved` 的 lesson。

**为什么是 git 版本账**：授权是运行时治理，不是用户的笔记，所以不进 `MIND_ROOT`；
但每一次状态变化都必须可核、可回滚，所以用一份 git 仓库记历史（用户 11-16 点头）。
没有 `git` 可执行时降级为 `.history/` 快照，绝不因为记历史失败就挡住表态。

**不持全局记忆**（裁定 215 #34）：这里只放「哪一类我可以自己定」的授权边界，
不放某一摊的事实；后者仍归各首席，指挥要问就用 `ask`。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

PROPOSED = "proposed"
APPROVED = "approved"
REJECTED = "rejected"
REVOKED = "revoked"
STATUSES = (PROPOSED, APPROVED, REJECTED, REVOKED)

#: 授权的两种来源（裁定 227）。``conversation`` = 用户**这一轮亲口说的**授权句，
#: 那句话本身就是点头，所以直接生效；``comment`` = 从评论/回执里读出来的意图，
#: 只是草稿，仍要人点头。模型不许把后者冒充前者。
SOURCE_CONVERSATION = "conversation"
SOURCE_COMMENT = "comment"
SOURCES = (SOURCE_CONVERSATION, SOURCE_COMMENT)

#: 只有生效状态参与召回；proposed 绝不能被当成「用户已经同意」。
EFFECTIVE = frozenset({APPROVED})

DIR_NAME = "lesson-ledger"
FILE_NAME = "lessons.json"
HOME_ENV = "PLOBI_HOME"


@dataclass
class Lesson:
    id: str
    scope: str
    rule: str
    question: str = ""
    evidence: str = ""
    decision_ids: list[str] = field(default_factory=list)
    status: str = PROPOSED
    created_at: float = 0.0
    approved_at: float = 0.0
    approved_by: str = "user"
    #: 凭什么放权：记下用户原话（对话授权）或关联的表态 id（评论回路），可核、可撤回。
    source: str = SOURCE_COMMENT
    user_words: str = ""
    #: 后来点头／驳回／撤回时用户说的那句话（裁定 228：Agent 全套工具必须带原话）。
    decision_words: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _home() -> Path:
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_plobi_home

        return Path(get_plobi_home())
    except Exception:  # noqa: BLE001
        return Path.home() / ".plobi"


def _tokens(text: str) -> set[str]:
    """中文按相邻二字切、英文数字按词切。只用于**召回**，不做放行判断。"""
    raw = str(text or "").lower()
    chars = "".join(
        ch if (ch.isalnum() or "\u4e00" <= ch <= "\u9fff") else " " for ch in raw
    )
    out: set[str] = set()
    for word in chars.split():
        if word.isascii():
            out.add(word)
            continue
        out.update(word[i:i + 2] for i in range(len(word) - 1))
        if len(word) == 1:
            out.add(word)
    return out


def similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class LessonLedger:
    def __init__(self, home: Optional[Path | str] = None) -> None:
        self.root = Path(home) / DIR_NAME if home is not None else _home() / DIR_NAME
        self.path = self.root / FILE_NAME
        # 惰性建目录：读口也要查这份账，读不能顺手在盘上造目录。

    # -- store ---------------------------------------------------------

    def _read(self) -> list[Lesson]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [Lesson(**blob) for blob in (raw.get("lessons") or []) if isinstance(blob, dict)]

    def _write(self, rows: Iterable[Lesson], message: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "lessons": [r.as_dict() for r in rows]}
        if self.path.exists():
            history = self.root / ".history"
            history.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(self.path, history / f"{stamp}.json")
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
        self._commit(message)

    def _commit(self, message: str) -> bool:
        """git 版本；拿不到 git 就只留 `.history/` 快照，不挡住人表态。"""
        try:
            if not (self.root / ".git").exists():
                subprocess.run(
                    ["git", "init", "-q", str(self.root)],
                    check=True, capture_output=True, timeout=20,
                )
            subprocess.run(
                ["git", "-C", str(self.root), "add", "--", FILE_NAME],
                check=True, capture_output=True, timeout=20,
            )
            subprocess.run(
                ["git", "-C", str(self.root), "commit", "-q", "-m", message],
                check=True, capture_output=True, timeout=20,
            )
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("lesson ledger git history unavailable: %s", exc)
            return False

    # -- writes --------------------------------------------------------

    def propose(
        self, *, scope: str, rule: str = "", question: str = "", evidence: str = "",
        decision_ids: Optional[list[str]] = None,
        source: str = SOURCE_COMMENT, user_words: str = "",
    ) -> Lesson:
        """起草一条授权；**默认不生效**。拿不准就只写 question，不编 rule。

        裁定 227：``source=conversation`` 且带了 ``user_words``，表示这是用户
        **这一轮亲口说的**授权——那句话本身就是点头，直接落 ``approved``，不必
        再去收件箱点一次；原话存进账本供回核。其余一律 ``proposed``。
        """
        source = str(source or SOURCE_COMMENT).strip().lower()
        if source not in SOURCES:
            raise ValueError(f"unknown lesson source: {source!r}")
        words = " ".join(str(user_words or "").split())[:500]
        if source == SOURCE_CONVERSATION and not words:
            # 没有原话就不许冒充用户授权——这是「不许模型自评」在入口这侧的牙。
            raise ValueError("对话授权必须带 user_words（用户原话），否则只能按 comment 起草草稿")

        from_user = source == SOURCE_CONVERSATION and bool(words)
        now = time.time()
        row = Lesson(
            id="les_" + uuid.uuid4().hex[:12],
            scope=str(scope or "general")[:200],
            rule=str(rule or "")[:500],
            question=str(question or "")[:500],
            evidence=(words if from_user else str(evidence or ""))[:500],
            decision_ids=[str(x) for x in (decision_ids or [])][:20],
            status=APPROVED if from_user else PROPOSED,
            created_at=now,
            approved_at=now if from_user else 0.0,
            approved_by="user_utterance" if from_user else "user",
            source=source,
            user_words=words,
        )
        verb = "authorize" if from_user else "propose"
        self._write(list(self._read()) + [row], f"{verb} {row.id}")
        return row

    def _set(self, lesson_id: str, status: str, *, by: str = "user") -> Optional[Lesson]:
        if status not in STATUSES:
            raise ValueError(f"unknown lesson status: {status!r}")
        rows = self._read()
        for row in rows:
            if row.id != lesson_id:
                continue
            row.status = status
            if status == APPROVED:
                row.approved_at = time.time()
                row.approved_by = str(by or "user")[:80]
            self._write(rows, f"{status} {lesson_id}")
            return row
        return None

    def approve(self, lesson_id: str, *, by: str = "user") -> Optional[Lesson]:
        return self._set(lesson_id, APPROVED, by=by)

    def reject(self, lesson_id: str) -> Optional[Lesson]:
        return self._set(lesson_id, REJECTED)

    def revoke(self, lesson_id: str) -> Optional[Lesson]:
        """撤回一条已生效的授权：下次召回立刻不再命中。"""
        return self._set(lesson_id, REVOKED)

    def decide_with_words(
        self, lesson_id: str, status: str, *, user_words: str
    ) -> Optional[Lesson]:
        """Agent 全套工具用的状态变更入口（裁定 228）。

        与上面三枚「人点按钮」用的 approve/reject/revoke 分开：那条路人的点击就是证据；
        这一条模型在调，所以必须带用户原话，且原话要落账可核。**不许模型自评**。
        """
        if status not in (APPROVED, REJECTED, REVOKED):
            raise ValueError(f"unknown decision status: {status!r}")
        words = " ".join(str(user_words or "").split())[:500]
        if not words:
            raise ValueError("Agent 变更授权状态必须带 user_words（用户原话），不许自评")

        rows = self._read()
        for row in rows:
            if row.id != lesson_id:
                continue
            row.status = status
            row.decision_words = words
            if status == APPROVED:
                row.approved_at = time.time()
                row.approved_by = "user_utterance"
            self._write(rows, f"decide {status} {lesson_id} by utterance")
            return row
        return None

    # -- reads ---------------------------------------------------------

    def get(self, lesson_id: str) -> Optional[Lesson]:
        return next((r for r in self._read() if r.id == lesson_id), None)

    def all(self) -> list[Lesson]:
        return self._read()

    def approved(self) -> list[Lesson]:
        return [r for r in self._read() if r.status in EFFECTIVE]

    def recall(self, text: str, *, limit: int = 5) -> list[tuple[float, Lesson]]:
        """按语义召回**已生效**授权；proposed/rejected/revoked 一律不进结果。"""
        query = _tokens(text)
        if not query:
            return []
        scored = [
            (similarity(text, f"{r.scope} {r.rule}"), r) for r in self.approved()
        ]
        hits = [(score, row) for score, row in scored if score > 0]
        hits.sort(key=lambda pair: (-pair[0], pair[1].id))
        return hits[:max(1, int(limit))]
