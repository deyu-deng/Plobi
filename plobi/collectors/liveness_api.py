"""`GET /api/collect/sources` —— 每条信源最后一次真的落库在什么时候。

只把 :mod:`plobi.collectors.liveness` 那个探针挂到**既有的** `/api/collect` 前缀下
（不新增顶层面，§3.3）。探针本身是纯读，路由这层只负责"别用枚举没抛异常当活着"。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from plobi.collectors import registry
from plobi.collectors.liveness import read_source_liveness

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/sources", name="collect_sources")
async def collect_sources_route():
    """每条信源的活性读数 + 一句总结。

    `expected_sources` 取注册表那份名单（唯一真源），所以**新加的信源不会静默消失**：
    没采过也照样出一行 `never`，而不是"看着像没有这个信源"。
    """
    data = await run_in_threadpool(read_source_liveness, None,
                                   expected_sources=registry.source_ids())
    return {"ok": True, "data": data}
