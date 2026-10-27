"""幕布那条的执行方：名单走接口、正文走接口，**本机那份只当"能不能借登录态"**。

一轮的做法（2026-10-27 这台实测的调用形状）：

1. 从本机客户端的数据里读出今天那枚 JWT（:mod:`.localdb`；读不到就不建这一条，
   屏幕上因此是"未连接 + 客户端尚未在本机登录"，不是一片红）。
2. `get_all_documents_page {offset, limit}` 拿到根上那一串 `{id, type}`。
   根这一层**不回文档列表**（实测 ``documents: []``），所以要按 ``type`` 分头走：
   是 ``folder`` 就再问一次 `list/get {folderId}`，是文档就直接收。
3. 只对**本轮要交的**那几篇问 `document/get {docId}` 取正文——一篇一次请求，
   全量第一趟有上限（``MAX_BODIES_PER_ROUND``），之后按 `updateTime` 增量。

正文是 `definition` 那一串 JSON（`{"customStyle", "nodes":[{"id","text","children",…}]}`，
实测 17 339 字符那一片解得开）。这里**只把它摊成带缩进的行**进池，
不保留它的样式 JSON：池里那一条给人读、给模型读，样式回不去也不影响内容。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Optional

from ..base import Occurrence
from . import localdb

SOURCE_ID = "mubu"
#: 数据形状那一格（屏幕上那排 chip 的取值就是它，10-27 那条"形状降成标签"的口径）。
KIND_DOC = "doc"
#: 一轮最多取回几篇正文：第一趟全量时它是**节流阀**，不是"只采这么多篇"。
MAX_BODIES_PER_ROUND = 120
#: 单篇正文进池的字数上限（那一片 17k 字符的实测在范围内；更大的截断并在 evidence 里说明）。
MAX_BODY_CHARS = 40000
#: 目录递归的深度上限——它自己的树再深也不该让我们无限收请求。
MAX_DEPTH = 6


def _local_iso(raw: Any) -> str:
    """毫秒时间戳 → 本地无时区 ISO（与钉钉那侧同一个写法，池里时间只此一种形状）。"""
    try:
        moment = dt.datetime.fromtimestamp(int(raw) / 1000)
    except (TypeError, ValueError, OSError):
        return ""
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _since_ms(since: Any) -> int:
    if since is None:
        return 0
    if isinstance(since, (int, float)):
        return int(since) if since > 1e11 else int(since) * 1000
    text = str(since).strip()
    if not text:
        return 0
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return int(parsed.timestamp() * 1000)


def _plain(text: Any) -> str:
    """节点里那句"行内样式"摊成人话（这台实测：`text` 常是 `<span class="bold">课程内容</span>`）。

    样式本来就到池里来"给人读、给模型读"为止——留一串 HTML 在正文里，下一个读池的 Agent
    要把每一行都当富文本再解一次，而加粗本来也不 carries 信息。
    """
    raw = str(text or "")
    if "<" not in raw and "&" not in raw:
        return raw.strip()
    import html as _html
    import re

    stripped = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    stripped = re.sub(r"<[^>]+>", "", stripped)
    return _html.unescape(stripped).strip()


def outline(definition: Any) -> tuple[str, int]:
    """`definition` → `(带缩进的行, 节点数)`。解不开就交回能交的那一点。"""
    if isinstance(definition, str):
        try:
            definition = __import__("json").loads(definition)
        except ValueError:
            return definition.strip()[:MAX_BODY_CHARS], 0
    if not isinstance(definition, dict):
        return "", 0
    lines: list[str] = []

    def walk(nodes: Iterable[dict], depth: int) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            text = _plain(node.get("text"))
            if text:
                lines.append("  " * depth + "- " + " ".join(text.split()))
            children = node.get("children")
            if isinstance(children, list) and children:
                walk(children, depth + 1)

    root = definition.get("nodes")
    if isinstance(root, list):
        walk(root, 0)
    body = "\n".join(lines)
    return body[:MAX_BODY_CHARS], len(lines)


class MubuCollector:
    source_id = SOURCE_ID

    def __init__(self, api: Any, *, max_bodies: int = MAX_BODIES_PER_ROUND,
                 depth_limit: int = MAX_DEPTH) -> None:
        self.api = api
        self.max_bodies = max_bodies
        self.depth_limit = depth_limit
        #: 本轮的量：名单几篇、取回几篇正文、为什么少取。`health()` 拿它说话。
        self.roster_note = ""
        self.body_note = ""

    @classmethod
    def build(cls, *, token: Optional[str] = None, api: Any = None) -> "MubuCollector":
        """生产接线：`api` 是注入位（测试不碰网），默认用本机那枚登录态建一只真的。"""
        if api is None:
            from .api import MubuClient

            api = MubuClient(token if token is not None else localdb.read_token())
        return cls(api)

    def health(self) -> dict:
        """账号在不在、名单能不能问出来——两句分开说，不拿"能登录"冒充"采到了"。"""
        try:
            profile = self.api.profile()
        except localdb.LocalStoreUnavailable as exc:
            return {"ok": False, "state": exc.code, "detail": "本机客户端没有可用登录态"}
        except Exception as exc:  # noqa: BLE001 - 接口那一侧的失败形状不止一种
            return {"ok": False, "state": getattr(exc, "code", "network_unreachable"),
                    "detail": f"问不到账号：{type(exc).__name__}"}
        note = f"登录态可用（账号等级 {profile.get('level', '?')}）"
        return {"ok": True, "state": "ready",
                "detail": f"{note}；名单：{self.roster_note or '这一轮还没走过'}；"
                          f"正文：{self.body_note or '这一轮还没取过'}"}

    def pull(self, *, since: Any = None) -> list[Occurrence]:
        """全量或增量：名单每次都走一遍（一两趟请求），正文只取该取的那几篇。"""
        threshold = _since_ms(since)
        documents = self._roster()
        self.roster_note = f"{len(documents)} 篇"
        wanted = [row for row in documents if int(row.get("updateTime") or 0) > threshold] \
            if threshold else documents
        capped = wanted[:self.max_bodies]
        self.body_note = f"{len(capped)} 篇（本轮上限 {self.max_bodies}，名单共 {len(documents)}）" \
            if len(wanted) > self.max_bodies else f"{len(capped)} 篇"

        occurrences: list[Occurrence] = []
        for row in capped:
            doc_id = str(row.get("id") or "")
            if not doc_id:
                continue
            try:
                detail = self.api.document(doc_id)
            except Exception:  # noqa: BLE001 - 单篇取不到不该关掉整条源，也不该让它冒充"没有这一篇"
                continue
            body, nodes = outline(detail.get("definition"))
            name = str(detail.get("name") or row.get("name") or "").strip()
            if not name:
                continue
            occurrences.append(Occurrence(
                source=self.source_id,
                external_id=f"doc:{doc_id}",
                title=name,
                start_at=_local_iso(detail.get("updateTime") or row.get("updateTime")),
                kind=KIND_DOC,
                evidence={
                    "text": body,
                    "doc_id": doc_id,
                    "folder_id": str(row.get("folderId") or ""),
                    "path": " / ".join(str(step.get("name") or "") for step in (detail.get("directory") or []) if isinstance(step, dict)),
                    "node_count": nodes,
                    "base_version": detail.get("baseVersion"),
                    "updated_at": detail.get("updateTime") or row.get("updateTime"),
                    "truncated": len(body) >= MAX_BODY_CHARS,
                    "origin": "api2.mubu.com",
                },
            ))
        return occurrences

    def _roster(self) -> list[dict]:
        """根 + 各层目录里的文档，按 `id` 去重（同一篇挂在两个地方只算一次）。"""
        found: dict[str, dict] = {}

        def add(rows: Iterable[dict]) -> None:
            for row in rows:
                # `deleted` 那一格是真的在回（这台实测每条都带 0/1）：回收站里的那些不算一篇在用的文档。
                if isinstance(row, dict) and row.get("id") and not int(row.get("deleted") or 0):
                    found.setdefault(str(row["id"]), row)

        def visit_folder(folder_id: str, depth: int) -> None:
            if depth > self.depth_limit:
                return
            try:
                children = self.api.folder(folder_id)
            except Exception:  # noqa: BLE001 - 一个目录问不通不该把整份名单判死
                return
            add(children.get("documents") or [])
            for folder in children.get("folders") or []:
                if isinstance(folder, dict) and folder.get("id"):
                    visit_folder(str(folder["id"]), depth + 1)

        try:
            roots = self.api.document_ids()
        except localdb.LocalStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            self.roster_note = f"问不到（{getattr(exc, 'code', type(exc).__name__)}）"
            return []

        add([row for row in roots if row.get("type") != "folder"])
        for row in [r for r in roots if r.get("type") == "folder"][: self.max_bodies]:
            visit_folder(str(row["id"]), 1)
        return list(found.values())


__all__ = ["KIND_DOC", "MubuCollector", "SOURCE_ID", "outline"]
