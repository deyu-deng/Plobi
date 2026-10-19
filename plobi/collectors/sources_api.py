"""信源那一面（设置里的「信源」区）的**唯一** HTTP 入口，挂既有 `/api/collect` 前缀。

`GET /api/collect/sources` 是 10-15 就有的活性探针那一格，这里**给它加字段而不是新开
一条读入口**——面上只该有一个地方能读到"某条信源现在什么状态"，两个入口就是两份
可能不一致的读数。活性那半（`read_source_liveness`）一个字没改，分类 / 开关 / 执行方
三格是**这一层**从注册表与用户配置层补上去的。

动词那一半（`POST .../enabled`、`POST .../try`、`POST .../setup`）是新增的，
理由写在 `Docs/specs/source-layer.md` §6.1：采集器不是 HTTP 常驻服务，面上没有任何
办法在浏览器里直接驱动它，所以"试一次"必须有路可走。

**返回的每一句为什么都是现成的那一句**：P6 那条「不许描述不存在的机制」在这一层的
具体形状是——`try` 的 `detail` **逐字**来自 `sweep.py`，**逐字**来自采集器自己的状态
词表。这一层不转述、不总结、不代写。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plobi.collectors import registry, sources_config
from plobi.collectors.liveness import read_source_liveness

logger = logging.getLogger(__name__)

router = APIRouter()


class EnabledRequest(BaseModel):
    enabled: bool


class SetupRequest(BaseModel):
    #: 键值对，由这一层按**键名**拒收疑似密钥的那一格（`sources_config.SECRET_LIKE_KEYS`）。
    #: 刻意是 `dict[str, str]` 而不是 `dict[str, Any]`：类型本身就把嵌套结构挡在外面，
    #: 校验那一半不必再解释一遍为什么嵌套的东西该有自己的落点。
    params: dict[str, str]


def _require_registered(source_id: str) -> None:
    """面上**只能对注册表里有的那条**动手。`manual` 不在注册表里 ⇒ 404，不是 400。

    这一格是"这个名字没在注册表里登记过"，不是"你参数填错了"——两者的排障方向
    完全不同，所以状态码也不同。
    """
    try:
        registry.spec(source_id)
    except registry.UnknownSource:
        raise HTTPException(status_code=404, detail=f"{source_id} 不是一条信源") from None


def _enrich(row: dict[str, Any], switches: Optional[dict[str, Optional[bool]]]) -> dict[str, Any]:
    """给活性那一行补上注册表与用户配置层的那几格。

    `collecting` 在这里被**覆写**成生效值（用户翻过的优先），但 `enabled_origin` 同时
    留在行里：只给一个布尔而不说它从哪来，"我明明关了怎么还在跑"这句话就永远答不上。

    `switches` 是**读口已经算好的那一份**，不在这里第二次去问配置层：第二次问就是
    第二次可能抛，而这一层抛出去就没法把"读不到开关"说成一行读数了——那正是这格
    存在的理由。开关读不到时 `switches[sid]` 是 `None`，于是下面那一支照实回 `None`。
    """
    source_id = row["source"]
    if source_id not in registry.source_ids():
        # `manual` 那一档：不是平台，是**来源档位**（`registry.py` 头部那条注记）。
        # 键**照样给全**，值全 `None` ——面上不该靠 `key in row` 判"这是不是一条
        # 真信源"，那是要每一处渲染都记得写的隐式契约。给全 + 全 None 才是"没有值"，
        # 也正好是这一档该说的话：它登记在库里，但不归任何分类。
        return {**row, "category": None, "enabled": None, "enabled_origin": None,
                "has_builder": None, "requires_manual_setup": None, "setup": None,
                "setup_fields": []}

    spec = registry.spec(source_id)
    enabled = None if switches is None else switches.get(source_id)
    origin = None if enabled is None else ("config" if sources_config.entry(source_id) else "spec")

    return {
        **row,
        "category": spec.category,
        "enabled": enabled,
        "enabled_origin": origin,
        "has_builder": spec.builder is not None,
        "requires_manual_setup": spec.capabilities.get("requires_manual_setup"),
        "setup": None if enabled is None else sources_config.setup_params(source_id),
        # 声明照原样给，**label 不翻译**：界面先查语言目录的
        # `settings.sources.fields.<key>`，查不到才回这一格。这样第三方包零改动就能
        # 出现在界面上（只是没翻译），而我们自己那四条四语言齐全。
        "setup_fields": [
            {"key": f.key, "label": f.label, "kind": f.kind, "required": f.required, "help": f.help}
            for f in spec.setup_fields
        ],
    }


@router.get("/sources", name="collect_sources")
async def collect_sources_route():
    """每条信源的活性读数 + 分类 + 生效开关。**读数的那一份，不是界面上算的**。"""
    expected = registry.source_ids()
    switches: Optional[dict[str, Optional[bool]]] = {sid: None for sid in expected}
    try:
        for sid in expected:
            switches[sid] = sources_config.effective_enabled(sid)[0]
    except sources_config.SourceConfigError as exc:
        # 开关读不到就说读不到，**不静默回落成代码默认值**——那正是"我明明关了怎么
        # 还在采"那种查不出来的故障形状。所以是"每一条都读不到"（`None`），不是
        # "每一条都按代码默认"：后者会让面照常画出一个**看起来正常**的开关。
        logger.warning("collect/sources: config layer unreadable: %s", exc)

    data = read_source_liveness(None, expected_sources=expected, switches=switches)

    return {
        "ok": True,
        "data": {
            **data,
            "sources": [_enrich(row, switches) for row in data["sources"]],
            "categories": [
                {"id": category, "sources": members}
                for category, members in registry.sources_by_category().items()
            ],
        },
    }


@router.post("/sources/{source_id}/enabled", name="collect_source_enabled")
async def set_source_enabled(source_id: str, body: EnabledRequest):
    """翻一条信源的采集开关，落共享层的 `sources.yaml`。

    **下一次** sweep 才生效——当前这一轮不等，也不假装等：正在跑的那一轮读的是它
    开始时那份名单，中途翻开关等于让同一轮里两条的语义不一样。
    """
    _require_registered(source_id)
    try:
        enabled = sources_config.set_enabled(source_id, body.enabled)
    except sources_config.SourceConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "data": {"source": source_id, "enabled": enabled}}


@router.post("/sources/{source_id}/try", name="collect_source_try")
async def try_source(source_id: str):
    """跑**这一条**一轮真采集，把结果原样回给面。

    这一格是 §6.1 表里 C/D 两行的动词，也是唯一**不猜原因**的那一个：不承诺修好，
    只承诺跑一轮、然后把执行方自己报的 `state` / `detail` 显示出来。面那一侧
    **不许**根据 `state` 自己编句子（P6）。

    不查缓存、不排队：点了就跑，跑多久就等多久——用户问的是"现在到底行不行"。
    """
    _require_registered(source_id)

    from plobi.agenda import get_service
    from plobi.collectors.sweep import run_sweep

    try:
        service = get_service()
    except Exception as exc:  # noqa: BLE001 - 建不出 service 就是这一轮答不了，如实说
        raise HTTPException(status_code=503, detail=f"读不到日程库，跑不了这一轮：{exc}") from exc

    try:
        report = run_sweep(service=service, sources=(source_id,))
    except Exception as exc:  # noqa: BLE001 - 一次都不许把执行方的失败吞成"没数据"
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    row: Optional[dict[str, Any]] = next(
        (r for r in report["sources"] if r["source"] == source_id), None,
    )
    if row is None:
        raise HTTPException(status_code=502, detail=f"跑了一轮但 {source_id} 不在结果里")
    return {"ok": True, "data": row}


class ExternalInstallRequest(BaseModel):
    #: 用户敲的那一串。**这一层不解释它**——名字闸门在 CLI 那一层
    #: （`plobi collectors install`），这一层只 spawn，不碰 pip，也不解析包名。
    #: 理由在 `plobi/collectors/external.py` 模块头第 1 条：用户输入离执行最近的
    #: 一跳不该只隔一次函数调用。所以这里**故意不在 400 里做校验**——如果这里就拦了，
    #: 就等于把闸门又挪回了最近的那一跳，而底下那道就没人知道它存在。
    package: str
    upgrade: bool = True


@router.get("/sources/external", name="collect_sources_external")
async def list_external_sources():
    """外面装了哪些第三方信源包。

    从 `importlib.metadata` **现读**，不是记一份账——用户在终端里 `pip install` 的
    那个也要在这儿出现，否则界面上会少报，而少报的那份用户永远查不出来。
    """
    from plobi.collectors import external

    return {"ok": True, "data": {"packages": external.installed_external_packages()}}


@router.post("/sources/external/install", name="collect_source_external_install")
async def install_external_source(body: ExternalInstallRequest):
    """spawn `plobi collectors install <包名>`，把动作名与 pid 回给面。

    **不 await 那一轮**：装一个带依赖的包要几十秒，而 HTTP 请求挂在上面既超时又
    没有任何进度可看。面拿 `name` 去轮 `/api/actions/{name}/status` 尾巴——
    与 `plobi skills install` 那条路同一个形状。

    那一轮自己的成败由**退出码**说话，不合并成"成功"。
    """
    return _spawn_collectors_action(
        "install", body.package,
        extra=["--no-upgrade"] if not body.upgrade else [],
    )


@router.post("/sources/external/remove", name="collect_source_external_remove")
async def remove_external_source(body: ExternalInstallRequest):
    """spawn `plobi collectors remove <包名>`。同上，异步 + 尾巴。"""
    return _spawn_collectors_action("remove", body.package)


def _spawn_collectors_action(verb: str, package: str, *, extra: Optional[list[str]] = None) -> dict[str, Any]:
    """把一条 collectors 动作拉起来，并给它一个**独一份**的日志名。

    独一份的理由照 `_hub_action_name` 原话：`_spawn_plobi_action` 按名字追一个
    进程/日志，共用 "collectors-install" 会让并发两行互相覆盖状态——用户看到的是
    另一个包那一轮的进度，而那比不显示更难查。
    """
    from plobi_cli.web_server import _ACTION_LOG_FILES, _spawn_plobi_action

    digest = hashlib.sha1(f"{verb}:{package}".encode()).hexdigest()[:8]
    name = f"collectors-{verb}-{digest}"
    _ACTION_LOG_FILES.setdefault(name, f"action-{name}.log")

    try:
        proc = _spawn_plobi_action(
            ["collectors", verb, package, "--yes", *(extra or [])], name,
        )
    except Exception as exc:  # noqa: BLE001 - 起不来就要说，不许静默什么都不发生
        raise HTTPException(status_code=500, detail=f"起不来这一轮：{exc}") from exc

    return {"ok": True, "data": {"name": name, "package": package, "pid": proc.pid}}


@router.post("/sources/{source_id}/setup", name="collect_source_setup")
async def set_source_setup(source_id: str, body: SetupRequest):
    """落一条信源首次配置产出的**非密钥**参数。

    落盘成功就算配好了，**不替任何一方去"验证能不能连上"**：能不能连上是执行方自己
    的读数（`try` 那一轮会报它自己的状态），这一格只管存住用户填的东西。

    校验在 :mod:`plobi.collectors.sources_config` 那一层按**声明**做（不在声明里的
    键拒收、必填的缺了拒收）——表单是照那份声明画的，所以这一格收的东西与画出来
    的东西必然一致，不会长出一个"界面上没有、文件里有"的键。
    """
    _require_registered(source_id)
    try:
        saved = sources_config.set_setup_params(source_id, body.params)
    except sources_config.SourceConfigError as exc:
        # 400 = 你填的东西这一层不收（多半是密钥，或不在声明里）；503 = 这一层读不到/写不进。
        status = 400 if ("密钥" in str(exc) or "没声明" in str(exc) or "还缺" in str(exc)) else 503
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"ok": True, "data": {"source": source_id, "setup": saved}}