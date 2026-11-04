"""surface 的笔记/档案只读 + 笔记与日记写入口。

返回 ``{ok, data|error}`` 同一个信封：

* ``GET /api/surface/agents`` —— 24 张卡（名册身份 + 标准档案元数据，不带正文）
* ``GET /api/surface/agents/{name}`` —— 一页（标准档案正文）
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

from fastapi import APIRouter, Body, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


def _unavailable(what: str, exc: Exception) -> JSONResponse:
    return _error(503, f"{what} unavailable: {exc}")


@router.get("/surface/agents")
async def list_surface_agents():
    from plobi.mind.agent_dossier import build_index

    try:
        data = await run_in_threadpool(build_index)
    except Exception as exc:
        return _unavailable("surface dossier", exc)
    return {"ok": True, "data": data}


@router.get("/surface/agents/{name}")
async def get_surface_agent(name: str):
    from plobi.mind.agent_dossier import read_agent

    try:
        data = await run_in_threadpool(read_agent, name)
    except Exception as exc:
        return _unavailable("surface dossier", exc)
    if data is None:
        return _error(404, f"agent {name!r} is not registered")
    return {"ok": True, "data": data}


@router.get("/surface/notes")
async def list_surface_notes():
    from plobi.mind.notes_library import build_library

    try:
        data = await run_in_threadpool(build_library)
    except Exception as exc:
        return _unavailable("notes library", exc)
    return {"ok": True, "data": data}


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
    from plobi.mind.diary import list_entries

    try:
        data = await run_in_threadpool(list_entries)
    except Exception as exc:
        return _unavailable("diary", exc)
    return {"ok": True, "data": data}


@router.get("/surface/diary/{day}")
async def get_surface_diary_entry(day: str):
    from plobi.mind.diary import read_entry

    try:
        data = await run_in_threadpool(read_entry, day)
    except ValueError as exc:
        return _error(400, str(exc))
    except Exception as exc:
        return _unavailable("diary", exc)
    return {"ok": True, "data": data}


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
