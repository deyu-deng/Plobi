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
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

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
    """信源给的文本格：`None` 与缺字段都归成空串，别把 `None` 写进 `NOT NULL` 列。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    return str(value)


#: 列里只准出现的这一种形状（裁定 189 那条排序与 `since` 都靠它）。
CANONICAL_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 纯数字里除 epoch 之外还见过的两种紧凑日期。
_COMPACT_FORMATS = (
    ("%Y%m%d%H%M%S", 14),
    ("%Y%m%d", 8),
)


def normalize_time(value: Any) -> tuple[str, Optional[str]]:
    """**唯一的归一点**：任何时间形状 → naive 本地 `YYYY-MM-DD HH:MM:SS`。

    返回 ``(规范串, 原始串或 None)``。原始串只在进来的形状本来就不是这一形时带回来，
    由 :func:`land` 落到 ``context.sent_at_raw``——信源给的那一份不许丢，但**列里只准有一种形状**：
    `'T'`(0x54) 比 `' '`(0x20) 大，混着写时同一天前缀下谁排前面取决于谁先写入，
    `ORDER BY occurred_at DESC` 与 `since` 就都不再是它们看起来的那个意思。

    归一放在这一层而不是每个采集器里，是那条老判据（§3.2.1「只有全信源共用的形状才配当列」）
    的另一半：以后教务、课表、邮箱都往同一列写，**只归一一次**才不会各写一形。

    认不出来时**存空串并出声**，不拿原始串碰运气——空串在排序里自然沉底，
    而一条读不出的时间留在列里会污染整池的比较。抛 ``PoolError`` 不做：
    那会让一轮采集因为一条消息的时间读不出就整批失败，而这一格坏掉不该带走信源。
    """
    if value is None:
        return "", None
    if isinstance(value, datetime):
        return _render(value), None

    text = str(value).strip()
    if not text:
        return "", None

    digits = text[1:] if text.startswith("-") else text
    if digits.isdigit():
        parsed = _from_digits(digits)
    else:
        parsed = _from_text(text)

    if parsed is None:
        logger.warning("plobi pool: 时间那一格读不出来（%r）——存空串，原样进日志", text)
        return "", text

    canonical = _render(parsed)
    return canonical, (text if text != canonical else None)


def _render(moment: datetime) -> str:
    """aware → 本机时区的 naive；naive 照原样（不替用户猜他那个不带偏移的时间是哪个时区）。"""
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.replace(microsecond=0).strftime(CANONICAL_FORMAT)


def _from_digits(digits: str) -> Optional[datetime]:
    """纯数字那几形：秒级 epoch 10 位、毫秒级 13 位，紧凑日期 8／14 位。"""
    try:
        if len(digits) == 10:
            return datetime.fromtimestamp(int(digits))
        if len(digits) == 13:
            return datetime.fromtimestamp(int(digits) / 1000)
    except (OverflowError, OSError, ValueError):
        return None
    for fmt, width in _COMPACT_FORMATS:
        if len(digits) == width:
            try:
                return datetime.strptime(digits, fmt)
            except ValueError:
                return None
    return None


def _from_text(text: str) -> Optional[datetime]:
    """ISO 那一族（带不带 `T`、带不带偏移、只给日期都算），再退一步试几种斜杠写法。"""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


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
                "SELECT kind, body, title, context, visibility, conversation, occurred_at,"
                " captured_at FROM pool_items"
                " WHERE source = ? AND external_id = ?",
                (item.source, item.external_id),
            ).fetchone()

            occurred, occurred_raw = normalize_time(item.occurred_at)
            captured, _captured_raw = normalize_time(item.captured_at)
            # 信源原样那一份不许丢，但只在这一格真的不是那一形时才留（否则我们自己写
            # `_now()` 的每一条都会带一份没用的副本）。采集器自己写了就不覆盖它。
            context = dict(item.context or {})
            if occurred_raw is not None and "sent_at_raw" not in context:
                context["sent_at_raw"] = occurred_raw
            context_json = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
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
                        occurred, captured or _now(), _now(),
                    ),
                )
                created += 1
                continue

            # 内容一模一样 ⇒ 不动 `updated_at`。否则**每轮重跑都把整池的 updated_at 刷一遍**，
            # 而 Agent 若按那一列排序就会看到一个永远在变的池子。
            # 参与比较的必须是 UPDATE 会写的那些**内容**格（`captured_at`／`updated_at` 是时钟，不算）。
            # 裁 189 那句「旧行不迁移、重采同一窗口时 `occurred_at` 自己变对」就靠这一格参加比较：
            # 只改时间而正文一个字没动的那次重采，以前会被判成「没变」而 `continue` 掉。
            if (
                existing["body"] == item.body
                and existing["title"] == item.title
                and existing["context"] == context_json
                and existing["visibility"] == visibility
                and existing["conversation"] == conversation
                and existing["kind"] == item.kind
                and existing["occurred_at"] == occurred
            ):
                continue

            conn.execute(
                "UPDATE pool_items SET kind = ?, title = ?, body = ?, context = ?,"
                " visibility = ?, conversation = ?, occurred_at = ?, captured_at = ?, updated_at = ?"
                " WHERE source = ? AND external_id = ?",
                (
                    item.kind, item.title, item.body, context_json,
                    visibility, conversation,
                    occurred or existing["captured_at"], _now(), _now(),
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

    `since` 什么形状都能给（走同一枚 :func:`normalize_time`），语义是**「从什么时候收到」**
    （裁定 189）。没有时间的行——含时间读不出来被存成空串的那些——**永远算在窗口内**：
    宁可多给一条说不清时间的，也不许把一条真收到的消息藏起来。读不出的 `since` 报
    ``PoolError``，不退化成「不过滤」（那会把「筛错了」伪装成「池里就这些」）。

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
        # `since` 走同一个归一点：写侧只准一种形状，读侧就不能拿 `'...T00:00:00'` 去和
        # `'... 00:00:00'` 比——`'T'` 比空格大，那样同一天内的行会被整日判成"还没到"。
        # 读不出来时**出声**，不退化成「不过滤」（那会把"筛错了"伪装成"池里就这些"）。
        since_value, _since_raw = normalize_time(since)
        if not since_value:
            raise PoolError(
                f"`since` 这个时间读不出来：{since!r}——要 `YYYY-MM-DD HH:MM:SS`、"
                "ISO（带不带 T／偏移都行）或 epoch 秒／毫秒。"
            )
        clauses.append("(occurred_at = '' OR occurred_at >= ?)")
        params.append(since_value)
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