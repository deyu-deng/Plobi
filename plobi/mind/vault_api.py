"""vault 的只读 + 日记写入口（WP-VAULT-AGENTS-VIEW）。

四条读路由、一条写路由，返回 §5 同一个信封 ``{ok, data|error}``：

* ``GET /api/vault/agents`` —— 24 张卡（名册身份 + 标准档案元数据，不带正文）
* ``GET /api/vault/agents/{name}`` —— 一页（标准档案正文）
* ``GET /api/vault/notes`` —— 知识库：全部笔记的元数据（用户 10-24：只放笔记）
* ``GET /api/vault/notes/content?path=`` —— 一篇笔记的正文
* ``GET /api/vault/diary`` / ``GET /api/vault/diary/{date}`` —— 日记目录与某一天
* ``PUT /api/vault/diary/{date}`` —— 保存某一天（落盘 + 只提交这一篇）

读取全在 ``plobi.mind.*``（同步、窄读）；这里只做线程池与状态码。名册/根不可用时
返回 503 而不是空表——空表会被读成「一个都没有」。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


def _unavailable(what: str, exc: Exception) -> JSONResponse:
    return _error(503, f"{what} unavailable: {exc}")


@router.get("/vault/agents")
async def list_vault_agents():
    from plobi.mind.agent_dossier import build_index

    try:
        data = await run_in_threadpool(build_index)
    except Exception as exc:
        return _unavailable("vault dossier", exc)
    return {"ok": True, "data": data}


@router.get("/vault/agents/{name}")
async def get_vault_agent(name: str):
    from plobi.mind.agent_dossier import read_agent

    try:
        data = await run_in_threadpool(read_agent, name)
    except Exception as exc:
        return _unavailable("vault dossier", exc)
    if data is None:
        return _error(404, f"agent {name!r} is not registered")
    return {"ok": True, "data": data}


@router.get("/vault/notes")
async def list_vault_notes():
    from plobi.mind.notes_library import build_library

    try:
        data = await run_in_threadpool(build_library)
    except Exception as exc:
        return _unavailable("notes library", exc)
    return {"ok": True, "data": data}


@router.get("/vault/notes/content")
async def get_vault_note(path: str = Query(..., min_length=1)):
    from plobi.mind.notes_library import read_note

    try:
        data = await run_in_threadpool(read_note, path)
    except Exception as exc:
        return _unavailable("notes library", exc)
    if data is None:
        return _error(404, f"note {path!r} does not exist under the notes root")
    return {"ok": True, "data": data}


@router.get("/vault/diary")
async def list_vault_diary():
    from plobi.mind.diary import list_entries

    try:
        data = await run_in_threadpool(list_entries)
    except Exception as exc:
        return _unavailable("diary", exc)
    return {"ok": True, "data": data}


@router.get("/vault/diary/{day}")
async def get_vault_diary_entry(day: str):
    from plobi.mind.diary import read_entry

    try:
        data = await run_in_threadpool(read_entry, day)
    except ValueError as exc:
        return _error(400, str(exc))
    except Exception as exc:
        return _unavailable("diary", exc)
    return {"ok": True, "data": data}


@router.put("/vault/diary/{day}")
async def put_vault_diary_entry(day: str, body: dict):
    from plobi.mind.diary import save_entry

    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        return _error(400, "body must be {\"text\": \"...\"}")
    try:
        data = await run_in_threadpool(save_entry, day, text)
    except ValueError as exc:
        return _error(400, str(exc))
    except Exception as exc:
        return _unavailable("diary", exc)
    return {"ok": True, "data": data}
