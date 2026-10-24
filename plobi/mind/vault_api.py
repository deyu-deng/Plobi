"""vault 档案的只读 HTTP 面（WP-VAULT-AGENTS-VIEW）。

两条路由，返回 §5 同一个信封 ``{ok, data|error}``：

* ``GET /api/vault/agents`` —— 24 张卡（名册身份 + 标准档案的元数据，不带正文）
* ``GET /api/vault/agents/{name}`` —— 一页（标准档案正文，按类别固定两三份）

读取全在 ``plobi.mind.agent_dossier``（同步、窄读）；这里只做线程池与状态码。
名册或根不可用时返回 503 而不是空表——空表会被读成「一个 Agent 都没有」。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


@router.get("/vault/agents")
async def list_vault_agents():
    from plobi.mind.agent_dossier import build_index

    try:
        data = await run_in_threadpool(build_index)
    except Exception as exc:
        return _error(503, f"vault dossier unavailable: {exc}")
    return {"ok": True, "data": data}


@router.get("/vault/agents/{name}")
async def get_vault_agent(name: str):
    from plobi.mind.agent_dossier import read_agent

    try:
        data = await run_in_threadpool(read_agent, name)
    except Exception as exc:
        return _error(503, f"vault dossier unavailable: {exc}")
    if data is None:
        return _error(404, f"agent {name!r} is not registered")
    return {"ok": True, "data": data}
