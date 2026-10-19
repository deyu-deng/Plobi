"""信息池 —— 采集器**唯一**的落点（`specs/source-pool.md`）。

用户 10-19 原话：「不要直接在日程库里面落东西……收集信息进信息池里面。添加日程是由
任意的 Agent 自主用相应工具读取池里面的信息……而不是信息直接去修改日程。」

所以这个库**只进不出**：采集器往这儿写，Agent 读，**没有任何自动路径从这儿往
`agenda.db` 搬东西**。池→日程那一步必须经过 Agent 的判断与它手里的日程工具——
那不是这一层的事，这一层连 `agenda.db` 在哪儿都不想知道。

三件照着 `agenda/store.py` 抄、或者抄它的注记：

- **落点在共享层**（裁定 42）：`get_shared_plobi_root()` 那一格，与 `agenda.db` 同级。
  **不进分身家**——理由照 `agenda_db_path` 的注记：二十四个分身各存一份就是新的漂移源，
  而那次事故的表现正是「问『我明天有什么』得到的是空」。
- **唯一键 `(source, external_id)`**：撞了更新，不长第二份。照 `ingest._row_keys` 的做法，
  因为「重跑一轮不能长出第二份」是同一道判据。
- **`context` 是一格 JSON，信源各自的形状放进去**（talker、群名、发送人…）。**但
  `visibility` 与 `conversation` 是列**（裁定 187 第 1 条）：那两格不是某个信源的私有
  形状，是所有信源共用的"这一条来自哪一扇窗口、对谁可见"，而读侧要按它筛。
  判据留在这儿：**只有全信源共用的形状才配当列**，其余一律进 `context`。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

_INITIALIZED: set[str] = set()
_INIT_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_items (
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'note',
    title       TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    context     TEXT NOT NULL DEFAULT '{}',
    visibility  TEXT NOT NULL DEFAULT '',
    conversation TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    acted       INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_pool_occurred ON pool_items(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_pool_source_kind ON pool_items(source, kind);
"""

#: 这两列是裁定 187 第 1 条点名要「真存」的那两格：
#: `visibility`（私聊／群聊／将来还有"一对一之外"）与 `conversation`（哪一扇窗口）。
#: 存 `context` 里不算——那一格是自由 JSON，读侧要筛就得每条先解析一遍，
#: 而窗口／可见性是所有信源共用的形状，不是某个信源的私有条目。
#:
#: **一次一形**（用户 10-20 拍）：**已经躺在池里的那 12 条留空，以后写侧带上才填**。
#: 不做回填——回填要从采集器的内存里去重窗口倒推，而这一刀不许碰采集器与 `source_talker`
#: （那第三列被他驳了）。空串就是"这一条当时没记窗口"，不是"这条不属于任何窗口"。
ADDED_COLUMNS = ("visibility", "conversation")


#: 放在函数定义之前：这些是 :func:`query` 的**默认值**，定义在文件尾部的话
#: `def query(limit=DEFAULT_LIMIT)` 在 import 时就 NameError（真踩过一次）。
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
class PoolError(RuntimeError):
    """池子读不到 / 写不进——出声，不静默吞。"""


def path() -> Path:
    """共享层那一份 `pool.db`。**解析不到就报错**，不退回分身家。"""
    try:
        from plobi_constants import get_shared_plobi_root

        root = Path(get_shared_plobi_root()) / "plobi"
    except Exception as exc:  # noqa: BLE001 - 共享根读不到就是这一层不可用
        raise PoolError(f"读不到共享家目录，信息池不可用：{exc}") from exc

    return root / "pool.db"


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """把后加的列补到已经存在的库上——`CREATE TABLE IF NOT EXISTS` 对旧库什么都不做。

    这台盘上的 `pool.db` 是先建起来的（12 条真数据），只改 SCHEMA 的话那些库永远拿不到
    新列，写侧一写就 `no column named visibility`。加完不回填：**旧的留空、带上才填**。
    """
    have = {row["name"] for row in conn.execute("PRAGMA table_info(pool_items)")}
    for column in ADDED_COLUMNS:
        if column in have:
            continue
        conn.execute(
            f"ALTER TABLE pool_items ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
        )


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """开一份连（WAL + row factory），schema 只初始化一次。"""
    resolved = Path(db_path) if db_path else path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(resolved), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    key = str(resolved.resolve())
    if key not in _INITIALIZED:
        with _INIT_LOCK:
            if key not in _INITIALIZED:
                conn.executescript(SCHEMA)
                _add_missing_columns(conn)
                conn.commit()
                _INITIALIZED.add(key)

    return conn


@dataclass(frozen=True)
class PoolItem:
    """池子里的一行。**`acted` 不是「已处理」** —— 池子不是队列，见规格 §3.5。"""

    source: str
    external_id: str
    kind: str = "note"
    title: str = ""
    body: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    visibility: str = ""
    conversation: str = ""
    occurred_at: str = ""
    captured_at: str = ""
    acted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "acted": self.acted,
            "body": self.body,
            "captured_at": self.captured_at,
            "context": self.context,
            "conversation": self.conversation,
            "external_id": self.external_id,
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "title": self.title,
            "visibility": self.visibility,
        }


def _now() -> str:
    # `sep=" "` 而不是 `sep="T"`：库里 `events.created_at` 那一族存的就是
    # `YYYY-MM-DD HH:MM:SS`，池子跟着用同一种形状 ⇒ 排序与比较都现成，
    # 不用在读的地方再各自解析一次。
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _text(value: Any) -> str:
    """时间那一格容得下好几种形状 —— 信源自带的时区戳、naive ISO、epoch 都见过。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    return str(value)


def land(items: Iterable[PoolItem], *, db_path: Optional[Path] = None) -> dict[str, Any]:
    """把这一轮采到的东西落进池子。返回 `{created, updated, unchanged}`。

    **没有 `agenda.db` 参数，也没有 `apply` 参数**——这一层刻意不提供「顺手落日程」的
    出口。上一版的 `ingest_occurrences(service, …, apply=…)` 就是那个出口，正是它让
    「信息直接去修改日程」成了默认行为；把参数拿掉，那条路就断了，而不是靠约定。
    """
    conn = connect(db_path)
    created = updated = 0

    try:
        for item in items:
            existing = conn.execute(
                "SELECT body, title, context, visibility, conversation, captured_at FROM pool_items"
                " WHERE source = ? AND external_id = ?",
                (item.source, item.external_id),
            ).fetchone()

            context_json = json.dumps(item.context, ensure_ascii=False, sort_keys=True, default=str)
            visibility = _text(item.visibility)
            conversation = _text(item.conversation)

            if existing is None:
                conn.execute(
                    "INSERT INTO pool_items (source, external_id, kind, title, body, context,"
                    " visibility, conversation, occurred_at, captured_at, acted, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                    (
                        item.source, item.external_id, item.kind, item.title, item.body,
                        context_json, visibility, conversation,
                        _text(item.occurred_at), _text(item.captured_at) or _now(), _now(),
                    ),
                )
                created += 1
                continue

            # 内容一模一样 ⇒ 不动 `updated_at`。否则**每轮重跑都把整池的 updated_at 刷一遍**，
            # 而 Agent 若按那一列排序就会看到一个永远在变的池子。
            if (
                existing["body"] == item.body
                and existing["title"] == item.title
                and existing["context"] == context_json
                and existing["visibility"] == visibility
                and existing["conversation"] == conversation
            ):
                continue

            conn.execute(
                "UPDATE pool_items SET kind = ?, title = ?, body = ?, context = ?,"
                " visibility = ?, conversation = ?, occurred_at = ?, captured_at = ?, updated_at = ?"
                " WHERE source = ? AND external_id = ?",
                (
                    item.kind, item.title, item.body, context_json,
                    visibility, conversation,
                    _text(item.occurred_at) or existing["captured_at"], _now(), _now(),
                    item.source, item.external_id,
                ),
            )
            updated += 1

        conn.commit()
    finally:
        conn.close()

    return {"created": created, "updated": updated, "unchanged": 0}


def query(
    *,
    source: Optional[str] = None,
    kind: Optional[str] = None,
    visibility: Optional[str] = None,
    conversation: Optional[str] = None,
    since: Optional[str] = None,
    contains: Optional[str] = None,
    acted: Optional[bool] = None,
    limit: int = DEFAULT_LIMIT,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Agent 读池的那一格。**返回正文**（用户 10-19 拍「放开」）。

    `visibility` / `conversation` 按**精确值**筛（裁定 187 那两列）；要模糊找文字用
    `contains`。空串筛不出"当时没记窗口"的那批——省略参数就是都要，这是刻意的：
    「没记」不是一种可见性，也不是一扇窗口。

    `limit` 有上限不是因为隐私，是 prompt：一次回几万条正文会把上下文撑爆，
    而那与隐私无关但同样要防。默认给一个保守值。
    """
    capped = max(1, min(int(limit), MAX_LIMIT))
    clauses: list[str] = []
    params: list[Any] = []

    if source:
        clauses.append("source = ?")
        params.append(source)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if visibility:
        clauses.append("visibility = ?")
        params.append(visibility)
    if conversation:
        clauses.append("conversation = ?")
        params.append(conversation)
    if since:
        clauses.append("(occurred_at = '' OR occurred_at >= ?)")
        params.append(since)
    if contains:
        clauses.append("(title LIKE ? OR body LIKE ?)")
        params.extend([f"%{contains}%", f"%{contains}%"])
    if acted is not None:
        clauses.append("acted = ?")
        params.append(1 if acted else 0)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(capped)

    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT source, external_id, kind, title, body, context, visibility,"
            f" conversation, occurred_at,"
            f" captured_at, acted FROM pool_items{where}"
            " ORDER BY COALESCE(NULLIF(occurred_at, ''), captured_at) DESC, external_id DESC LIMIT ?",
            params,
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "acted": bool(row["acted"]),
            "body": row["body"],
            "captured_at": row["captured_at"],
            "conversation": row["conversation"],
            "external_id": row["external_id"],
            "kind": row["kind"],
            "occurred_at": row["occurred_at"],
            "source": row["source"],
            "title": row["title"],
            "visibility": row["visibility"],
        }
        try:
            item["context"] = json.loads(row["context"])
        except (TypeError, ValueError):
            item["context"] = {}

        out.append(item)

    return out


def mark_acted(source: str, external_id: str, acted: bool = True, *, db_path: Optional[Path] = None) -> bool:
    """记「Agent 拿它动过日程了」。**只是一个记号**，不是队列的出队（规格 §3.5）。"""
    conn = connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE pool_items SET acted = ?, updated_at = ? WHERE source = ? AND external_id = ?",
            (1 if acted else 0, _now(), source, external_id),
        )
        conn.commit()

        return cursor.rowcount > 0
    finally:
        conn.close()


def stats(*, db_path: Optional[Path] = None) -> dict[str, Any]:
    """池子现在什么样：总数、按信源、按类型。**现读**，不是记的账。"""
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM pool_items").fetchone()["n"]
        by_source = {
            row["source"]: row["n"]
            for row in conn.execute("SELECT source, COUNT(*) AS n FROM pool_items GROUP BY source ORDER BY source")
        }
        by_kind = {
            row["kind"]: row["n"]
            for row in conn.execute("SELECT kind, COUNT(*) AS n FROM pool_items GROUP BY kind ORDER BY kind")
        }
        acted = conn.execute("SELECT COUNT(*) AS n FROM pool_items WHERE acted = 1").fetchone()["n"]
    finally:
        conn.close()

    return {"acted": acted, "by_kind": by_kind, "by_source": by_source, "total": total}