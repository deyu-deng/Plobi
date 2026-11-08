"""surface 的笔记/档案只读 + 笔记与日记写入口。

返回 ``{ok, data|error}`` 同一个信封：

* ``GET /api/surface/agents`` —— 24 张卡（名册身份 + 标准档案元数据，不带正文）
* ``GET /api/surface/agents/{name}`` —— 一页（标准档案正文）
* ``GET /api/surface/agents/{name}/tasks`` —— 那一页的每日任务清单（``tasks.json``）
* ``POST /api/surface/agents/{name}/tasks/toggle`` —— 人勾选／取消勾选（改同一份 ``tasks.json``）
* ``GET /api/surface/notes`` —— 知识库：全部笔记的元数据
* ``GET /api/surface/notes/content?path=`` —— 一篇笔记的正文
* ``PUT /api/surface/notes/content?path=`` —— 人在笔记栏里整篇存回（``create=true`` 时新建）
* ``GET /api/surface/diary`` / ``GET /api/surface/diary/{date}`` —— 日记目录与某一天
* ``PUT /api/surface/diary/{date}`` —— 保存某一天（落盘 + 只提交这一篇）

读取全在 ``plobi.mind.*``（同步、窄读）；这里只做线程池与状态码。名册/根不可用时
返回 503 而不是空表——空表会被读成「一个都没有」。

笔记写入**不套** :mod:`plobi.mind.notes_contract` 那道闸：它管的是 Agent 写，
模块头注里明写「不管人自己写」。路径安全与并发保护在 ``notes_library.save_note`` 里。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


def _unavailable(what: str, exc: Exception) -> JSONResponse:
    return _error(503, f"{what} unavailable: {exc}")


def _root_gone(what: str, root: str) -> JSONResponse | None:
    """人读面根不在盘上 ⇒ 回 503，**不回空表**（模块头那句：空表会被读成「一个都没有」）。

    为什么两条腿都要查：DB 只是人读面的**索引**——索引指着一个**读不到**的源，
    那它给出的行数既可能是陈的、也可能只是索引自己的残影；屏上分不出这两者。
    宁可直接说「读不到」，也别让一个空表或者一行陈账被读成「你只有这些」。

    （指针没钉时 ``real_target`` 会落到一个盘上不存在的旧布局目录——
    那不是「没有笔记」，是「找不到笔记在哪儿」。）
    """
    if root and not Path(root).exists():
        return _unavailable(what, FileNotFoundError(f"{what} root does not exist: {root}"))
    return None


@router.get("/surface/agents")
async def list_surface_agents():
    """优先从数据库查询 agent_dossiers 表；没有则降级到文件系统扫描。

    **两条腿返回的形状必须一致**（前端 `AgentDossierCard` / `AgentProgressPane` 只认一种）。
    DB 腿给的是**元数据索引**（里程碑数、工作路径、更新时间）；正文档列表（docs）
    那部分 DB 里没有 ⇒ 走文件系统腿补齐，**不让 DB 那一半变成一个更瘦的形状**。
    """
    from plobi.mind.agent_dossier import build_index as fs_build_index

    # 1. 先试数据库
    try:
        from .vault import list_dossiers

        dossiers = list_dossiers()
        if dossiers:
            from plobi.agents.registry import AgentRegistry
            registry = AgentRegistry.load()

            # 文件系统腿的底稿（docs 那一组只有它有）——DB 腿在上面补里程碑与路径
            fs_index = await run_in_threadpool(fs_build_index)
            fs_agents = {a.get("id"): a for a in (fs_index.get("agents") or [])}

            agents_data = []
            for dossier in dossiers:
                entry = registry.get(dossier.agent_id)
                if not entry:
                    continue
                base = dict(fs_agents.get(dossier.agent_id) or {})
                if not base:
                    continue
                base["milestones"] = {
                    "total": dossier.milestones_total,
                    "done": dossier.milestones_done,
                }
                if dossier.work_path:
                    base["workPath"] = dossier.work_path
                if dossier.updated:
                    base["updated"] = dossier.updated
                agents_data.append(base)

            if agents_data:
                return {"ok": True, "data": {"agents": agents_data}}
    except Exception as e:
        logger.warning("vault.db query failed: %s; falling back to filesystem", e)

    # 2. 降级：直接读文件系统
    try:
        data = await run_in_threadpool(fs_build_index)
        return {"ok": True, "data": data}
    except Exception as exc:
        return _unavailable("surface dossier", exc)


@router.get("/surface/agents/{name}")
async def get_surface_agent(name: str):
    """优先从数据库查询 agent_dossiers 表；没有则降级到文件系统扫描。

    DB 里存的是 plan/progress 的**正文**；文档元数据（文件名、时间、摘要）仍以文件系统为准，
    两条腿拼出来的形状与纯文件系统腿一致。
    """
    from plobi.mind.agent_dossier import read_agent as fs_read_agent, resolve_dossier_id

    # **作用域哨兵先翻译成名册 id**（桌面那边 L1 的哨兵是 ``'l1'``，名册里 L1
    # 那一行的 name 是 ``secretary``）：两条腿都用同一个 id，否则档案卡去要
    # ``/api/surface/agents/l1`` ⇒ 404「agent 'l1' is not registered」。
    name = resolve_dossier_id(name)

    # 1. 先试数据库
    try:
        from .vault import get_dossier

        dossier = get_dossier(name)
        if dossier:
            from plobi.agents.registry import AgentRegistry
            registry = AgentRegistry.load()

            # `AgentRegistry` 只有 `get(name)`，**没有 `get_agent`**（旧代码调它 ⇒ AttributeError
            # ⇒ 每次都落回文件系统，DB 白建）
            entry = registry.get(name)
            if not entry:
                return _error(404, f"agent {name!r} is not registered")

            # 文件系统腿做底稿：docs 那一组（key/filename/chars/updated）只有它有
            base = await run_in_threadpool(fs_read_agent, name)
            if not base:
                return _error(404, f"agent {name!r} is not registered")

            # 用 DB 里的正文替换同名文档的 text
            by_key = {d.get("key"): d for d in (base.get("docs") or [])}
            for key, content in (("plan", dossier.plan_content), ("progress", dossier.progress_content)):
                if not content:
                    continue
                doc = by_key.get(key)
                if doc is None:
                    doc = {
                        "key": key,
                        "label": "计划" if key == "plan" else "进展",
                        "filename": "plan.md" if key == "plan" else "progress.md",
                        "exists": True,
                        "chars": len(content),
                        "updated": dossier.updated,
                    }
                    by_key[key] = doc
                    base.setdefault("docs", []).append(doc)
                doc["text"] = content[:131072]  # MAX_DOC_BYTES = 128KB
                doc["exists"] = True

            base["milestones"] = {
                "total": dossier.milestones_total,
                "done": dossier.milestones_done,
            }
            if dossier.work_path:
                base["workPath"] = dossier.work_path
            if dossier.updated:
                base["updated"] = dossier.updated

            return {"ok": True, "data": base}
    except Exception as e:
        logger.warning("vault.db query failed: %s; falling back to filesystem", e)

    # 2. 降级：直接读文件系统
    try:
        data = await run_in_threadpool(fs_read_agent, name)
        if data is None:
            return _error(404, f"agent {name!r} is not registered")
        return {"ok": True, "data": data}
    except Exception as exc:
        return _unavailable("surface dossier", exc)


@router.get("/surface/agents/{name}/tasks")
async def get_surface_agent_tasks(name: str):
    """那一页的每日任务清单（档案目录下的 ``tasks.json``，与 plan/progress 同目录）。

    屏上的进度就是这份文件：人勾选 ⇒ ``POST …/toggle`` 改它；Agent 用
    ``surface_write`` 改**同一个**文件 ⇒ 两边共用一处真源。

    坏文件不许糊成空表：JSON 坏了 ⇒ ``data.error`` 有值（前端显示「读不到」），
    文件不在 ⇒ ``data.exists`` 为 False（前端显示「还没有清单」）。
    """
    from plobi.mind.agent_dossier import resolve_dossier_id
    from plobi.mind.dossier_tasks import (
        AgentNotRegistered,
        DossierRootUnavailable,
        find_home,
        read_tasks,
        tasks_path,
    )

    # **作用域哨兵先翻译成名册 id**（桌面那边 L1 的哨兵是 ``'l1'``，名册里 L1
    # 那一行的 name 是 ``secretary``）：档案跟着 Agent 走，不翻译就去查 ``'l1'``
    # ⇒ 404「agent 'l1' is not registered」。
    name = resolve_dossier_id(name)

    try:
        home = await run_in_threadpool(find_home, name)
    except AgentNotRegistered as exc:
        return _error(404, str(exc))
    except DossierRootUnavailable as exc:
        return _unavailable("surface dossier tasks", exc)

    if home is None:
        # 这一格压根没绑档案目录 ⇒ 没有清单可读（**不是**读不到）。
        return {"ok": True, "data": {"exists": False, "error": None, "days": []}}

    data = await run_in_threadpool(read_tasks, tasks_path(home))
    return {"ok": True, "data": data.to_dict()}


@router.post("/surface/agents/{name}/tasks/toggle")
async def toggle_surface_agent_task(name: str, body: dict):
    """人勾选／取消勾选一条任务；改的是同一份 ``tasks.json``（Agent 写的那份）。

    ``done`` 是**目标状态**，不是翻转——前端少传一次请求不会把状态带偏。
    """
    from plobi.mind.agent_dossier import resolve_dossier_id
    from plobi.mind.dossier_tasks import (
        AgentNotRegistered,
        DossierRootUnavailable,
        TaskNotFound,
        TasksFileBroken,
        TasksFileMissing,
        find_home,
        tasks_path,
        toggle_task,
    )

    name = resolve_dossier_id(name)

    payload = body if isinstance(body, dict) else {}
    day = str(payload.get("date") or "").strip()
    item_id = str(payload.get("id") or "").strip()
    done = payload.get("done")
    if not day or not item_id:
        return _error(400, 'body must be {"date": "...", "id": "...", "done": true|false}')
    if not isinstance(done, bool):
        return _error(400, '"done" must be true or false (it is the target state, not a toggle)')

    try:
        home = await run_in_threadpool(find_home, name)
    except AgentNotRegistered as exc:
        return _error(404, str(exc))
    except DossierRootUnavailable as exc:
        return _unavailable("surface dossier tasks", exc)

    if home is None:
        return _error(404, f"agent {name!r} has no dossier to hold tasks")

    try:
        data = await run_in_threadpool(toggle_task, tasks_path(home), day, item_id, done)
    except TasksFileMissing as exc:
        return _error(404, str(exc))
    except TaskNotFound as exc:
        return _error(404, str(exc))
    except TasksFileBroken as exc:
        return _error(409, str(exc))
    except Exception as exc:
        return _unavailable("surface dossier tasks", exc)
    return {"ok": True, "data": data.to_dict()}


@router.get("/surface/notes")
async def list_surface_notes():
    """优先从数据库查询 vault_notes 表；没有则降级到文件系统扫描。

    DB 腿给的是**每篇笔记的元数据**；``topics``／``totals`` 这两格是清单级的聚合，
    由文件系统腿算（它本来就要扫一遍目录）⇒ 两条腿的形状一致。
    """
    from plobi.mind.notes_library import build_library as fs_build_library

    # 1. 先试数据库
    try:
        from .vault import list_notes

        notes = list_notes()
        if notes:
            # 聚合格（topics／totals）仍以文件系统腿为准
            base = await run_in_threadpool(fs_build_library)
            gone = _root_gone("notes", str(base.get("root") or ""))
            if gone is not None:
                return gone
            topics = base.get("topics") or []
            totals = dict(base.get("totals") or {})

            return {
                "ok": True,
                "data": {
                    "root": base.get("root") or get_vault_root(),
                    "notes": [
                        {
                            "path": n.path,
                            "title": n.title,
                            "topic": n.topic,
                            "tags": n.tags,
                            "belongs": n.belongs,
                            "updated": n.updated,
                            "chars": n.chars,
                            "excerpt": n.excerpt
                        }
                        for n in notes
                    ],
                    "topics": topics,
                    "tags": list(set(t for n in notes for t in n.tags)),
                    "totals": {
                        "scanned": totals.get("scanned", len(notes)),
                        "listed": len(notes),
                        "skipped": totals.get("skipped", 0),
                        "hidden": totals.get("hidden", 0),
                    }
                }
            }
    except Exception as e:
        logger.warning("vault.db query failed: %s; falling back to filesystem", e)

    # 2. 降级：直接读文件系统
    try:
        data = await run_in_threadpool(fs_build_library)
        gone = _root_gone("notes", str(data.get("root") or ""))
        if gone is not None:
            return gone
        return {"ok": True, "data": data}
    except Exception as exc:
        return _unavailable("notes library", exc)


def get_vault_root() -> str:
    """Get the vault notes root path."""
    try:
        from plobi.mind.paths import real_target
        return str(real_target("Surface/notes"))
    except Exception:
        return "/unknown"


@router.get("/surface/notes/search")
async def search_surface_notes(q: str = Query(..., min_length=1)):
    """Search notes by title/excerpt."""
    try:
        from .vault import search_notes
        
        notes = await run_in_threadpool(search_notes, q, limit=50)
        return {
            "ok": True,
            "data": {
                "query": q,
                "count": len(notes),
                "notes": [
                    {
                        "path": n.path,
                        "title": n.title,
                        "topic": n.topic,
                        "tags": n.tags,
                        "updated": n.updated,
                        "chars": n.chars,
                        "excerpt": n.excerpt
                    }
                    for n in notes
                ]
            }
        }
    except Exception as exc:
        return _error(503, f"search unavailable: {exc}")


@router.get("/surface/notes/content")
async def get_surface_note(path: str = Query(..., min_length=1)):
    from plobi.mind.notes_library import read_note

    try:
        data = await run_in_threadpool(read_note, path)
    except Exception as exc:
        return _unavailable("notes library", exc)
    if data is None:
        return _error(404, f"note {path!r} does not exist under the notes root")
    return {"ok": True, "data": data}


@router.put("/surface/notes/content")
async def put_surface_note(
    body: dict = Body(...),
    path: str = Query(..., min_length=1),
):
    """人在笔记栏里编辑后整篇存回；``create=true`` 时允许落到还不存在的文件。

    ``base`` 是编辑器打开时那份原文：盘上被别处改过就 409，绝不整篇覆盖。
    ``create`` 必须显式给——保存已有笔记时路径拼错，不该静默写出一个新文件。
    """
    from plobi.mind.notes_library import NoteConflict, resolve_note, save_note

    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        return _error(400, 'body must be {"text": "..."}')
    base = body.get("base")
    if base is not None and not isinstance(base, str):
        return _error(400, '"base" must be a string when present')
    create = body.get("create") is True

    if not create and resolve_note(path) is None:
        return _error(404, f"note {path!r} does not exist under the notes root")
    try:
        data = await run_in_threadpool(save_note, path, text, base_text=base)
    except NoteConflict as exc:
        return _error(409, str(exc))
    except ValueError as exc:
        return _error(400, str(exc))
    except Exception as exc:
        return _unavailable("notes library", exc)
    return {"ok": True, "data": data}


@router.get("/surface/diary")
async def list_surface_diary():
    """优先从数据库查询 user_diary 表；没有则降级到文件系统扫描。

    形状与文件系统腿一致：``{root, entries[], today}``。
    （旧版本这里返回的是「日期 → {date, exists, updated}」的字典，
    与 ``diary.list_entries()`` 的形状对不上 ⇒ 前端按前者的形状写的代码读不到。）
    """
    from plobi.mind.diary import list_entries as fs_list_entries

    # 1. 先试数据库
    try:
        from .vault import list_diaries

        entries = await run_in_threadpool(list_diaries)
        if entries:
            base = await run_in_threadpool(fs_list_entries)
            gone = _root_gone("diary", str(base.get("root") or ""))
            if gone is not None:
                return gone
            rows = []
            for entry in entries:
                day = entry.date
                try:
                    weekday = datetime.strptime(day, "%Y-%m-%d").strftime("%A")
                except ValueError:
                    weekday = ""
                rows.append({
                    "date": day,
                    "weekday": weekday,
                    "filename": f"{day}-{weekday}.md" if weekday else "",
                    "chars": len(entry.content or ""),
                    "updated": (entry.updated or "")[:10],
                })
            rows.sort(key=lambda row: row["date"], reverse=True)
            return {"ok": True, "data": {"root": base.get("root", ""), "entries": rows,
                                         "today": base.get("today", "")}}
    except Exception as e:
        logger.warning("vault.db query failed: %s; falling back to filesystem", e)

    # 2. 降级：直接读文件系统
    try:
        data = await run_in_threadpool(fs_list_entries)
        gone = _root_gone("diary", str(data.get("root") or ""))
        if gone is not None:
            return gone
        return {"ok": True, "data": data}
    except Exception as exc:
        return _unavailable("diary", exc)


@router.get("/surface/diary/{day}")
async def get_surface_diary_entry(day: str):
    """优先从数据库查询 user_diary 表；没有则降级到文件系统扫描。"""
    from plobi.mind.diary import read_entry as fs_read_entry

    # 1. 先试数据库
    try:
        from .vault import get_diary

        entry = await run_in_threadpool(get_diary, day)
        if entry and entry.content:
            # 形状与 ``diary.read_entry()`` 一致（date／weekday／filename／exists／text／updated）
            base = await run_in_threadpool(fs_read_entry, day)
            base = dict(base or {})
            base["text"] = entry.content
            base["exists"] = True
            if entry.updated:
                base["updated"] = entry.updated[:10]
            return {"ok": True, "data": base}
    except Exception as e:
        logger.warning("vault.db query failed: %s; falling back to filesystem", e)

    # 2. 降级：直接读文件系统
    try:
        data = await run_in_threadpool(fs_read_entry, day)
        return {"ok": True, "data": data}
    except Exception as exc:
        return _unavailable("diary", exc)


@router.put("/surface/diary/{day}")
async def put_surface_diary_entry(day: str, body: dict):
    from plobi.mind.diary import save_entry

    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        return _error(400, "body must be {\"text\": \"...\"}")
    # ``base`` = 编辑器打开时那份原文。带上它，保存前就能把"这期间浮层快记的那几条"
    # 合并回来，而不是被整篇覆盖掉；不带就是不要求合并。
    base = body.get("base")
    if base is not None and not isinstance(base, str):
        return _error(400, '"base" must be a string when present')
    try:
        data = await run_in_threadpool(save_entry, day, text, base_text=base)
    except ValueError as exc:
        return _error(400, str(exc))
    except Exception as exc:
        return _unavailable("diary", exc)
    return {"ok": True, "data": data}


@router.post("/surface/diary/{day}/capture")
async def capture_surface_diary_entry(day: str, body: dict):
    """路过记一句：往当天那篇末尾接一行 ``- HH:MM …``，记完浮层就收。"""
    from plobi.mind.diary import append_entry

    line = body.get("line") if isinstance(body, dict) else None
    if not isinstance(line, str):
        return _error(400, "body must be {\"line\": \"...\"}")
    try:
        data = await run_in_threadpool(append_entry, day, line)
    except ValueError as exc:
        return _error(400, str(exc))
    except Exception as exc:
        return _unavailable("diary", exc)
    return {"ok": True, "data": data}
