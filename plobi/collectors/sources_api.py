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

from plobi.collectors import cadence, icons, registry, sources_config
from plobi.collectors.liveness import read_source_liveness

logger = logging.getLogger(__name__)

router = APIRouter()

#: 数"这一条在池里几条"时最多扫多少行。到顶就说明这一族多到不需要精确数（界面上写"300+"）。
_POOL_SCAN_LIMIT = 5000


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


#: 三态的闭集（用户 10-22：「一个信源就一个开关按钮，就三种形态：未连接和连接，然后出错了会有一个报错」）。
#:
#: **为什么折态这一件事长在后端**：前端那张 7 条的胶囊表（`sources/verbs.ts`）是"读数 + 判据"
#: 混在一处算的，于是同一件事在屏上有七种话、在日志里有另一种，而"到底连没连上"这句话
#: 谁都能改一遍。这一层已经有全部输入（开关、声明、台账里上一轮的状态与那句话、活性、池里的条数），
#: 算完给出去，前端就只画。判据：**前端不再出现任何状态词表**。
STATUS_CONNECTED = "connected"
STATUS_DISCONNECTED = "disconnected"
STATUS_ERROR = "error"

#: 上一轮那些状态词 → 三态。**没列进来的词一律落 `disconnected` 而不是 `connected`**：
#: 一个我们没见过的状态词被画成绿点，就是下一颗假绿（裁 191 那颗"没测到就别说成测到"的同一把尺）。
_ERROR_STATES = frozenset({
    "auth_failed", "captcha_required", "network_unreachable", "empty_term", "parse_failed",
    "ics_parse_failed", "collect_failed", "failed", "error",
})
_CONNECTED_STATES = frozenset({"live", "ready", "ok", "not_due", "per_round", "throttled"})


def status_of(*, enabled: Optional[bool], needs_setup: bool, last_state: str,
              liveness_state: str, has_builder: bool) -> tuple[str, str]:
    """(状态, 那一格的原因码)。原因码是给界面查语言目录用的，**不是给人看的句子**（裁定 45.4）。

    次序是判据的一部分，所以写在一个函数里而不是散成 `if`：
    开关没点时"上一轮失败了"不是新闻（本来就没在跑），先报它等于把"我自己关的"画成"坏了"。
    """
    if enabled is None:
        return STATUS_DISCONNECTED, "config_unreadable"       # 读不到 ≠ 没点：界面不许把它画成关着
    if not enabled:
        return STATUS_DISCONNECTED, "off"
    if not has_builder:
        return STATUS_ERROR, "internal"                        # 注册表说在采却没人构造得出：我们的缺陷
    if needs_setup:
        return STATUS_DISCONNECTED, "needs_setup"
    if last_state in _ERROR_STATES:
        return STATUS_ERROR, last_state
    if liveness_state == "no_store":
        return STATUS_ERROR, "internal"
    if liveness_state == "live" or last_state in _CONNECTED_STATES:
        return STATUS_CONNECTED, "live" if liveness_state == "live" else last_state
    return STATUS_DISCONNECTED, "never_ran"


def _pool_counts(source_id: str) -> tuple[int, dict[str, int]]:
    """这一条在**信息池**里几条、按 `kind` 怎么分。

    为什么必须新读这一格：屏上那个 `events` 计数读的是日程库（`liveness.py` 那句
    `FROM events GROUP BY source`），而按裁定 193 采集器的东西只进池 ⇒ 教务那 371 条
    在这一屏上永远是 0，"这条源到底采到东西没有"这句话在屏上问不到（10-22 现场）。
    两个数都留着：`events` 是"日程库里有多少条"，`pool_count` 是"池里有多少条"——
    它们是两句不同的话，合成一个数就是下一次那种误判。

    一次按 `source` 问，不按 kind 循环问：那一族索引是 `(source, kind)`，按 source 走一次
    就够，循环问等于把这一屏的开销乘以种类数。
    """
    from plobi.pool import query as pool_query

    try:
        rows = pool_query(source=source_id, limit=_POOL_SCAN_LIMIT)
    except Exception as exc:  # noqa: BLE001 - 池读不到就说读不到，-1 是"读不到"而不是"零条"
        logger.debug("sources: pool unreadable for %s: %s", source_id, exc)
        return -1, {}
    by_kind: dict[str, int] = {}
    for row in rows:
        by_kind[str(row.get("kind") or "")] = by_kind.get(str(row.get("kind") or ""), 0) + 1

    return sum(by_kind.values()), by_kind


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
        # 10-22 改判：**整行不给**，而不是"键给全、值全 None"。上一版那个形状在屏上留下
        # 了一个真实的坏读数——角标写「信源 6」而屏上只有 5 行，因为 `manual` 没有分类、
        # 画不出行，却被 `sources.length` 数了进去。一条画不出来的行不该占一个计数。
        # 判据也回到最简单的那句：这一屏列的是**注册表里有的那些条**。
        return None

    spec = registry.spec(source_id)
    enabled = None if switches is None else switches.get(source_id)
    origin = None if enabled is None else ("config" if sources_config.entry(source_id) else "spec")
    last = cadence.last_run(source_id)
    fields = [{"key": f.key, "label": f.label, "kind": f.kind, "required": f.required, "help": f.help}
              for f in spec.setup_fields]
    # 密钥那一档：值**永不出门**，只回"设过没有"。界面上那格因此是写入口，不是读数口。
    configured = sources_config.secret_configured(source_id)
    params = sources_config.setup_params(source_id)
    # 「还缺必填吗」两种都要问：普通格看盘上那份参数，密钥格看 `.env` 里设过没有——
    # 只看前者会让一条凭据早就配好的源永远显示"去设置"，那是一颗按了回到原地的按钮。
    needs_setup = any(f["required"] and not (
        configured.get(f["key"]) if f["kind"] == "secret" else str(params.get(f["key"]) or "").strip())
        for f in fields)
    pool_count, pool_kinds = _pool_counts(source_id)
    status, status_code = status_of(enabled=enabled, needs_setup=needs_setup,
                                    last_state=last["state"], liveness_state=row.get("state") or "",
                                    has_builder=spec.builder is not None)

    return {
        **row,
        "category": spec.category,
        "enabled": enabled,
        "enabled_origin": origin,
        # 每源一行频率（裁定 191 二）：**生效值 + 归属 + 下一次该打在几点**三格一起给。
        # 只给一句 `cadence` 不够——"我明明设成每天三点怎么还在每轮打"这句话只能由
        # `cadence_origin` 回答，与 `enabled_origin` 是同一个套路（裁定 94：一条事实一处真源，
        # 但一处真源要把"它从哪来"一起说清）。
        **cadence.cadence_env(source_id),
        "has_builder": spec.builder is not None,
        "requires_manual_setup": spec.capabilities.get("requires_manual_setup"),
        "setup": None if enabled is None else {k: v for k, v in params.items() if k not in configured},
        "secrets": configured,
        # 三态那一格（用户 10-22：「一个信源就一个开关按钮，就三种形态」）。**由这一层算**，
        # 前端不再留第二套词表；`status_detail` 只在出错态给，且逐字来自上一轮（不转述）。
        "status": status,
        "status_code": status_code,
        "status_detail": last["detail"] if status == STATUS_ERROR else "",
        "last_run_at": last["at"],
        "last_state": last["state"],
        # 池里的条数：这一屏原来只有日程库那个 `events` 计数，于是按裁 193 只进池的教务
        # 在这里永远是 0——"这条源采到东西没有"问不到。两个数各说各的话，不合成一个。
        "pool_count": pool_count,
        "pool_kinds": pool_kinds,
        # 图标：从这条**自己声明的那个网站**抓一次（没有声明就空串，界面退品牌表再退首字母）。
        "icon": icons.icon_data_url(source_id, spec.site) if spec.site else "",
        "site": spec.site,
        # 声明照原样给，**label 不翻译**：界面先查语言目录的
        # `settings.sources.fields.<key>`，查不到才回这一格。这样第三方包零改动就能
        # 出现在界面上（只是没翻译），而我们自己那四条四语言齐全。
        "setup_fields": fields,
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
    # `_enrich` 对不在注册表里的那一行回 `None`（`manual` 那一档）——这里**真的把它剔掉**，
    # 而不是留着让界面自己 filter：屏上那个「信源 6」的角标数的是这个数组的长度，
    # 留一行画不出来的东西进去，就是一个数不上号的角标（10-22 现场：角标 6、行 5）。
    rows = [item for item in (_enrich(row, switches) for row in data["sources"]) if item is not None]

    return {
        "ok": True,
        "data": {
            **data,
            "sources": rows,
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

    # 手点一次**也算碰过它了**：不记账的话，"每天三点"配上下午手点一次，晚上那轮会再去打
    # 一遍学校——频率问的是"多久碰一次"，不是"钟走到几点了"。节流那一格本来就是这么算的。
    cadence.mark_run(source_id, state=str(row.get("state") or ""))
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