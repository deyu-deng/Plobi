"""信源管理的六把原子（WP-SOURCE-TOOLS）：给 Agent 一双能看见、能开关、能触发采集的手。

**为什么有这一族**（用户 11-16 19:2x 原话：「现在信源有新的需求了。现在Capabilities有没有管理
信源的原子工具呢？」）。现核（11-16 19:2x）：注册表全量 **139 把原子工具、71 个门类**里，
信源/采集/池相关的**只有** ``pool_read`` / ``pool_mark``（读的是**池**，不是信源本身）；
管信源那一侧**没有工具**——只有设置屏走的 HTTP 面（``/api/collect``）⇒ **Agent 手里一把都没有**。

**六把，每把一件原子事**（裁定 185：参数里不许有 ``action``／``intent``／``mode`` 这类选分支的
东西；业务流程由模型用原子工具组合出来）：

===========================  =====================================================
工具                         一件什么事
===========================  =====================================================
``source_list``              列信源，每一格给**读数**（开关／缺哪一步／状态／频率／池里几条）
``source_read``              读**一条**信源的详情（声明了哪几格、哪几格已设、密钥设过没有…）
``source_enable``            翻这一条的采集开关（并真的拉起／收掉那条腿）
``source_set_cadence``       改这一条多久采一次（cron 句；空串＝每轮都采）
``source_configure``         落这一条的配置格（声明成密钥的走 ``.env``）
``source_collect``           触发**这一条**跑一轮真采集，把执行方自己报的话原样回
===========================  =====================================================

**通用**：不为每种信源长一个新工具（裁定 187①）——一条微信与一条教务走同一条路。
**边界**：采集仍然只写池，池→日程归 Agent 自己判断（裁 186）；本族一个字都不碰日程库。
**值永不出门**：凭据写进 ``.env``，回包里只有「设过没有」，不回显、不进日志、不进返回值。

**复用哪几层真源**（同一件事不写第二遍）：状态词表走 ``sources_api.status_of()``（与设置屏
同一把尺）；开关走 ``sources_config.set_enabled()`` ＋ ``sidecar.apply()``；触发采集走
``sweep.run_sweep(sources=(id,))`` ＋ ``cadence.mark_run()``——**与设置屏那两格逐字同一路**。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

TOOLSET = "sources"


# --------------------------------------------------------------------------- #
# 读数那一层的小手：都从**真源**取，不在这儿重写一遍口径
# --------------------------------------------------------------------------- #

def _source_ids() -> tuple[str, ...]:
    """注册表里那些条（**发现要先做**：`_SPECS` 空着时一切读口都会"成功地"读到零条）。"""
    from plobi.collectors import registry as collectors

    collectors.ensure_discovered()
    return collectors.source_ids()


def _switches() -> Dict[str, Optional[bool]]:
    """生效开关。**读不到就是 ``None``**（不是 False）——"读不到"与"用户关了"是两件事。"""
    from plobi.collectors import sources_config

    out: Dict[str, Optional[bool]] = {}
    for sid in _source_ids():
        try:
            out[sid] = sources_config.effective_enabled(sid)[0]
        except sources_config.SourceConfigError:
            out[sid] = None
    return out


def _probe_of(spec) -> Optional[dict]:
    """这条信源自报的「它在这台机器上成不成立」。没声明就是 ``None``（不给每条都补一个说法）。"""
    if spec.probe is None:
        return None
    try:
        raw = dict(spec.probe() or {})
    except Exception as exc:  # noqa: BLE001 - 探针坏也要说得出，不许把整条读口崩掉
        logger.warning("source_tools: probe %s raised %s", spec.source_id, type(exc).__name__)
        return {"ready": False, "code": "probe_failed", "detail": f"{type(exc).__name__}: {exc}"}
    return {"ready": bool(raw.get("ready")), "code": str(raw.get("code") or ""),
            "detail": str(raw.get("detail") or "")}


def _guide_of(spec) -> Optional[dict]:
    """首次配置要**人肉**做的那一步（装软件 / 登录）——声明照原样给。"""
    guide = getattr(spec, "setup_guide", None)
    if guide is None:
        return None
    return {"action": guide.action, "detail": guide.detail, "title": guide.title, "url": guide.url}


def _live_rows(source_ids: tuple[str, ...]) -> Dict[str, dict]:
    """池的活性（每条最后一次进新条目是几点）。**读池，不读日程库**——两格各说各的话。"""
    from plobi.collectors.liveness import read_pool_liveness

    try:
        data = read_pool_liveness(None, expected_sources=source_ids, switches=_switches())
    except Exception as exc:  # noqa: BLE001 - 读不到池不是读不到信源
        logger.warning("source_tools: pool liveness unreadable: %s", type(exc).__name__)
        return {}
    return {str(r.get("source") or ""): r for r in (data.get("sources") or [])}


def _row(source_id: str, *, live: Optional[dict]) -> Dict[str, Any]:
    """一条信源现在什么读数。

    **状态词表只有一份**：``sources_api.status_of()``——与设置屏那一行同一把尺，
    所以屏上与这里不会给出两个答案。行里那几格**事实**取自同一批原语。
    """
    from plobi.collectors import cadence, registry, sources_config
    from plobi.collectors.sources_api import status_of

    spec = registry.spec(source_id)
    try:
        enabled = sources_config.effective_enabled(source_id)[0]
        enabled_origin = "config" if sources_config.entry(source_id) else "spec"
    except Exception as exc:  # noqa: BLE001 - 读不到就是读不到，不许回落成"用户关了"
        logger.warning("source_tools: switch unreadable for %s: %s", source_id, type(exc).__name__)
        enabled, enabled_origin = None, ""
    try:
        needs_setup = not sources_config.setup_ready(source_id)
    except Exception as exc:  # noqa: BLE001 - 读不到就当"还没配"，不把"读不到"说成"配好了"
        logger.warning("source_tools: setup state unreadable for %s: %s", source_id, type(exc).__name__)
        needs_setup = True
    last = cadence.last_run(source_id)
    status, status_code = status_of(
        enabled=enabled,
        needs_setup=needs_setup,
        last_state=str(last.get("state") or ""),
        liveness_state=str((live or {}).get("state") or ""),
        has_builder=spec.builder is not None,
        probe=_probe_of(spec),
        enabled_origin=enabled_origin,
    )
    return {
        "source": source_id,
        "display_name": spec.display_name or "",
        "summary": spec.summary or "",
        "category": spec.category,
        "enabled": enabled,
        "enabled_origin": enabled_origin,
        "needs_setup": needs_setup,
        "status": status,
        "status_code": status_code,
        "has_builder": spec.builder is not None,
        "pool_count": int((live or {}).get("events") or 0),
        "last_run_at": str(last.get("at") or ""),
        "last_state": str(last.get("state") or ""),
        "last_detail": str(last.get("detail") or ""),
        **cadence.cadence_env(source_id),
    }


def _detail(source_id: str) -> Dict[str, Any]:
    """一条信源的详情：声明了哪几格、哪几格已设、密钥设过没有、探针、人肉那一步、能力声明。"""
    from plobi.collectors import registry, sources_config

    spec = registry.spec(source_id)
    row = _row(source_id, live=_live_rows((source_id,)).get(source_id))
    secrets = sources_config.secret_configured(source_id)
    fields = []
    for field in spec.setup_fields:
        if field.kind == "secret":
            configured = bool(secrets.get(field.key))
        else:
            try:
                configured = sources_config.field_configured(source_id, field.key)
            except sources_config.SourceConfigError:
                configured = False
        fields.append({"key": field.key, "label": field.label, "kind": field.kind,
                       "required": field.required, "help": field.help,
                       "configured": configured})
    row["setup_fields"] = fields
    row["secrets"] = secrets
    row["probe"] = _probe_of(spec)
    row["setup_guide"] = _guide_of(spec)
    row["capabilities"] = dict(spec.capabilities or {})
    row["conversation_scoped"] = bool(spec.conversation_scoped)
    row["site"] = spec.site or ""
    return row


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _known_source(source_id: str):
    """认领这一条；不认识就回一句人话（不抛 HTTPException——那是设置屏那一层的形状）。"""
    from plobi.collectors import registry

    try:
        return registry.spec(source_id), None
    except registry.UnknownSource:
        return None, f"{source_id!r} 不是一条信源（注册表里没有这个名字）"


# --------------------------------------------------------------------------- #
# 六把工具
# --------------------------------------------------------------------------- #

SOURCE_LIST_SCHEMA: Dict[str, Any] = {
    "name": "source_list",
    "description": (
        "列本机认得的所有信源，每一格给**读数**：采不采（`enabled`）、这一格从哪来"
        "（`enabled_origin`：用户翻过 ＝ config，代码默认 ＝ spec）、缺不缺人肉那一步"
        "（`needs_setup`）、三态（`status`：connected / disconnected / error）与原因码"
        "（`status_code`）、多久采一次（`cadence`）与下一次（`next_run_at`）、池里已经几条"
        "（`pool_count`）、上一轮什么时候跑的（`last_run_at`）与它自己报的那句话。\n"
        "一个参数都不给。要看某一条的细节（要填哪几格、哪几格已经填了、密钥设过没有）用 `source_read`。"
    ),
    "parameters": {"type": "object", "properties": {}},
}

SOURCE_READ_SCHEMA: Dict[str, Any] = {
    "name": "source_read",
    "description": (
        "读**一条**信源的详情：`source_id` 是信源名（如 wechat / dingtalk / mubu / zju）。\n"
        "除了 `source_list` 给的那几格读数，这里还给：它声明了哪几格要填（`setup_fields`，"
        "每格带 `required` 与 `configured`）、哪几格是密钥且**设过没有**（`secrets`，"
        "**值永不回显**）、这台机器上它成不成立（`probe`）、首次配置要人肉做的那一步"
        "（`setup_guide`：装什么／去哪装）、以及它声明的能力（`capabilities`）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "哪一条信源。"},
        },
        "required": ["source_id"],
    },
}

SOURCE_ENABLE_SCHEMA: Dict[str, Any] = {
    "name": "source_enable",
    "description": (
        "翻一条信源的采集开关：`source_id` 是哪一条，`enabled` 是 true（开）还是 false（关）。\n"
        "**真的切断**：这一条自己声明了本机常驻服务（微信那个侧车）的，开的时候会把它拉起来、"
        "关的时候会把它收掉——所以关掉之后这一轮不会再碰它。\n"
        "下一次采集才生效：正在跑的那一轮读的是它开始时那份名单。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "哪一条信源。"},
            "enabled": {"type": "boolean", "description": "true 开、false 关。"},
        },
        "required": ["source_id", "enabled"],
    },
}

SOURCE_SET_CADENCE_SCHEMA: Dict[str, Any] = {
    "name": "source_set_cadence",
    "description": (
        "改一条信源**多久采一次**：`source_id` 是哪一条，`cadence` 是 cron 句"
        "（如 `\"0 3 * * *\"` 每天三点、`\"every 2h\"` 两小时一次）或 `\"\"`（每轮都采）。\n"
        "**写坏的那一句不落盘**：不认识的那一句原样退回给你，不进配置。\n"
        "频率管的是「多久碰它一次」，不是「钟走到几点了」——手点一次 `source_collect` 也算碰过。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "哪一条信源。"},
            "cadence": {"type": "string", "description": "cron 句；空串＝每轮都采。"},
        },
        "required": ["source_id", "cadence"],
    },
}

SOURCE_CONFIGURE_SCHEMA: Dict[str, Any] = {
    "name": "source_configure",
    "description": (
        "落一条信源的配置格：`source_id` 是哪一条，`params` 是**键值对**"
        "（如 `{\"address\": \"me@example.com\"}`）。键必须在它声明的那几格里，"
        "没声明的键不收。\n"
        "声明成密钥的那几格（`kind=secret`）走 `.env`，**值只落盘、不回显**："
        "回包里只有「设过没有」。留空＝不改那一格（清空要有别的动作）。\n"
        "必填格还缺着的，这一次会被拒——回你一句缺哪一格。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "哪一条信源。"},
            "params": {
                "type": "object",
                "description": "要落的那些格：键是它声明过的格名，值是字符串。",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["source_id", "params"],
    },
}

SOURCE_COLLECT_SCHEMA: Dict[str, Any] = {
    "name": "source_collect",
    "description": (
        "触发**这一条**信源跑一轮真采集：`source_id` 是哪一条。\n"
        "跑完回执行方自己报的那句话（`state` ＋ `detail`，逐字，不转述）与进池的条数。\n"
        "**不排队、不查缓存**：点了就跑，跑多久就等多久。采到的东西**只进信息池**，"
        "池→日程由读池的 Agent 自己判断。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "哪一条信源。"},
        },
        "required": ["source_id"],
    },
}


def _handle_list(args: Dict[str, Any], **_kw: Any) -> str:
    try:
        ids = _source_ids()
    except Exception as exc:  # noqa: BLE001 - 读不到就说读不到
        return _err(f"读不到信源名单：{type(exc).__name__}: {exc}")
    if not ids:
        return _err("注册表里一条信源都没有：要么采集器包没载入，要么这台机器上还没装任何一条")
    live = _live_rows(ids)
    try:
        rows = [_row(sid, live=live.get(sid)) for sid in ids]
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_tools: list failed: %s", type(exc).__name__)
        return _err(f"读信源读数失败：{type(exc).__name__}: {exc}")
    return _ok({"sources": rows, "count": len(rows)})


def _handle_read(args: Dict[str, Any], **_kw: Any) -> str:
    source_id = str(args.get("source_id") or "").strip()
    if not source_id:
        return _err("要哪一条？给一个 source_id")
    _, error = _known_source(source_id)
    if error:
        return _err(error)
    try:
        return _ok(_detail(source_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_tools: read %s failed: %s", source_id, type(exc).__name__)
        return _err(f"读这一条失败：{type(exc).__name__}: {exc}")


def _handle_enable(args: Dict[str, Any], **_kw: Any) -> str:
    from plobi.collectors import sidecar, sources_config

    source_id = str(args.get("source_id") or "").strip()
    spec, error = _known_source(source_id)
    if error:
        return _err(error)
    enabled = bool(args.get("enabled"))
    try:
        value = sources_config.set_enabled(source_id, enabled)
    except sources_config.SourceConfigError as exc:
        return _err(f"开关写不进去：{exc}")
    payload: Dict[str, Any] = {"source": source_id, "enabled": value}
    try:
        note = sidecar.apply(spec, enabled=value)
    except Exception as exc:  # noqa: BLE001 - 腿起不来也要说得出，开关那一格已经落了
        note = {"action": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    if note is not None:
        payload["sidecar"] = note
    return _ok(payload)


def _handle_set_cadence(args: Dict[str, Any], **_kw: Any) -> str:
    from plobi.collectors import sources_config

    source_id = str(args.get("source_id") or "").strip()
    _, error = _known_source(source_id)
    if error:
        return _err(error)
    value = str(args.get("cadence") or "").strip()

    if value:
        from cron.jobs import parse_schedule

        try:
            parse_schedule(value)
        except Exception as exc:  # noqa: BLE001 - 写坏的那一句**不落盘**
            return _err(f"这一句频率不认识：{value!r}（{exc}）。没写进去。")

    try:
        entries = sources_config.load()
        row = dict(entries.get(source_id) or {})
        row["cadence"] = value
        entries[source_id] = row
        sources_config.save(entries)
    except sources_config.SourceConfigError as exc:
        return _err(f"频率写不进去：{exc}")
    return _ok({"source": source_id, "cadence": value, "cadence_origin": "config"})


def _handle_configure(args: Dict[str, Any], **_kw: Any) -> str:
    from plobi.collectors import sources_config

    source_id = str(args.get("source_id") or "").strip()
    _, error = _known_source(source_id)
    if error:
        return _err(error)
    params = args.get("params")
    if not isinstance(params, dict) or not params:
        return _err("要落哪几格？给 params（键值对）")
    try:
        # **与已存的那几格合并**：只改一格时不必把必填那几格重填一遍
        # （首次配置那套校验要求必填格齐着——已存的就该算数）。
        merged = {**sources_config.setup_params(source_id), **{str(k): v for k, v in params.items()}}
        saved = sources_config.set_setup_params(source_id, merged)
        secrets = sources_config.secret_configured(source_id)
    except sources_config.SourceConfigError as exc:
        return _err(str(exc))
    return _ok({"source": source_id, "setup": saved, "secrets": secrets})


def _handle_collect(args: Dict[str, Any], **_kw: Any) -> str:
    from plobi.collectors import cadence
    from plobi.collectors.sweep import run_sweep

    source_id = str(args.get("source_id") or "").strip()
    _, error = _known_source(source_id)
    if error:
        return _err(error)

    try:
        from plobi.agenda import get_service

        service = get_service()
    except Exception:  # noqa: BLE001 - 没有它，采到池里的那几条照样进池
        service = None

    try:
        report = run_sweep(service=service, sources=(source_id,))
    except Exception as exc:  # noqa: BLE001 - 一次都不许把执行方的失败吞成"没数据"
        return _err(f"这一轮没跑成：{type(exc).__name__}: {exc}")

    row = next((r for r in report.get("sources") or [] if r.get("source") == source_id), None)
    if row is None:
        return _err(f"跑了一轮，但 {source_id} 不在结果里")

    # 手点一次**也算碰过它了**：频率问的是"多久碰一次"，不记这一下，
    # "每天三点"配上下午手点一次，晚上那轮会再去打一遍。
    cadence.mark_run(source_id, state=str(row.get("state") or ""), detail=str(row.get("detail") or ""))
    payload = {"source": source_id, "ok": bool(row.get("ok")),
               "state": str(row.get("state") or ""), "detail": str(row.get("detail") or "")}
    for key in ("created", "updated", "unchanged", "rows_seen"):
        if key in row:
            payload[key] = row[key]
    return _ok(payload)


# --------------------------------------------------------------------------- #
# 信源包那一段：列 / 装 / 卸（WP-SOURCE-PACKAGES）
#
# **闸门仍然在 CLI 里**：这一层只 spawn `plobi collectors …`，不碰 pip、
# **也不替 CLI 校验包名**——理由照 `plobi/collectors/external.py` 模块头第 1 条：
# 用户输入离执行最近的一跳不该只隔一次函数调用（自己先校验一遍＝把闸门挪回
# 最近的那一跳，底下那道就没人知道它存在）。设置屏那一面也是这么写的
# （`sources_api._spawn_collectors_action` 同样只 spawn）。
# --------------------------------------------------------------------------- #

#: pip 装一个带依赖的包要几十秒到几分钟；这一把**等它跑完**（Agent 要一个结果，
#: 不是一条「我起了，你自己去轮」的进度——Agent 手里没有「轮动作状态」那把工具）。
_PACKAGE_TIMEOUT = 600

SOURCE_PACKAGE_LIST_SCHEMA: Dict[str, Any] = {
    "name": "source_package_list",
    "description": (
        "列**外面装着的**第三方信源包（自己写的信源包，不是内置那几条）。\n"
        "每一格给：包名（`distribution`，**卸它就传这个**）、它带来的信源 id（`source`）、"
        "入口点名（`entry_point`）；包装上了但载不进来时，`load_error` 是那句原因。\n"
        "一个参数都不给。"
    ),
    "parameters": {"type": "object", "properties": {}},
}

SOURCE_PACKAGE_INSTALL_SCHEMA: Dict[str, Any] = {
    "name": "source_package_install",
    "description": (
        "装**一个**第三方信源包：`package` 是包名（如 `plobi-collector-telegram`）。\n"
        "**只收裸包名**：版本约束、extras、URL、git 地址一概不收（要去那些去终端）。\n"
        "装上之后它带来的那条信源**是关着的**——要采它，另外调 `source_enable`。\n"
        "装包＝跑那个包的代码，这一步会被循环守卫当成改动动作盯上。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "package": {"type": "string", "description": "包名。"},
        },
        "required": ["package"],
    },
}

SOURCE_PACKAGE_REMOVE_SCHEMA: Dict[str, Any] = {
    "name": "source_package_remove",
    "description": (
        "卸**一个**第三方信源包：`package` 是包名（`source_package_list` 给的那个 `distribution`）。\n"
        "卸掉之后它带来的那条信源会从注册表里消失；**采到的东西留在池里**（池只增不删）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "package": {"type": "string", "description": "包名。"},
        },
        "required": ["package"],
    },
}


def _spawn_collectors(verb: str, package: str) -> tuple[int, str, str]:
    """跑 ``plobi collectors <verb> <包名> --json --yes``，等它跑完。

    与设置屏那一面**同一个形状**（那一面也是 spawn `plobi collectors …`）——
    闸门在 CLI 里，不在这一层。`--json` 让 CLI 吐那份报告（它自己写的那句人话
    ＋ pip 的尾巴），`--yes` 标"这一条是非交互的"。
    """
    cmd = [sys.executable, "-m", "plobi_cli.main", "collectors", verb, package, "--json", "--yes"]
    env = {**os.environ, "PLOBI_NONINTERACTIVE": "1"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL,
                              timeout=_PACKAGE_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return 124, "", f"装/卸 {package} 超时（{_PACKAGE_TIMEOUT} 秒还没完）。"
    except Exception as exc:  # noqa: BLE001 - 起不来也要说得出
        return 127, "", f"起不来这一轮：{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _package_report(verb: str, package: str) -> Dict[str, Any]:
    """把 CLI 那一轮读成一份说得清的话（**CLI 自己说什么就是什么，不转述**）。"""
    code, out, err = _spawn_collectors(verb, package)
    report: Dict[str, Any] = {}
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except Exception:  # noqa: BLE001 - CLI 没吐 JSON 就按原文报
            continue
        if isinstance(parsed, dict):
            report = parsed
            break

    if not report:
        # CLI 没吐 JSON：它多半是**拒了**（名字闸门）——那句话就是判据，原样带回来。
        detail = (err or out or "").strip()
        return {"package": package, "verb": verb, "cli_exit_code": code, "ok": False,
                "summary": detail or f"装/卸 {package} 没跑成（退出码 {code}）。"}

    payload: Dict[str, Any] = {
        "package": str(report.get("package") or package),
        "verb": verb,
        "cli_exit_code": code,
        "ok": bool(report.get("ok")) and code == 0,
        "summary": str(report.get("summary") or ""),
        "sources_added": list(report.get("added") or []),
        "sources_removed": list(report.get("removed") or []),
        "sources": list(report.get("sources") or []),
    }
    output = report.get("output")
    if output:
        payload["output"] = output if isinstance(output, str) else list(output)
    return payload


def _package_result(payload: Dict[str, Any]) -> str:
    """成功与失败**一个形状**：失败时 `error` 那一格就是 CLI 自己写的那句话。"""
    out: Dict[str, Any] = {"ok": bool(payload.get("ok"))}
    if not out["ok"]:
        out["error"] = str(payload.get("summary") or "没跑成")
    out.update({k: v for k, v in payload.items() if k != "ok"})
    return json.dumps(out, ensure_ascii=False)


def _handle_package_list(args: Dict[str, Any], **_kw: Any) -> str:
    from plobi.collectors import external

    try:
        rows = external.installed_external_packages()
    except Exception as exc:  # noqa: BLE001 - 读不到就说读不到
        return _err(f"读外面装着的信源包失败：{type(exc).__name__}: {exc}")
    return _ok({"packages": rows, "count": len(rows)})


def _handle_package_install(args: Dict[str, Any], **_kw: Any) -> str:
    # **不预校验包名**（连空串也不拦）：名字闸门在 CLI 那一层，它自己会说明白
    # 「这一层不收什么」——自己先解释一遍＝把闸门挪回用户输入离执行最近的那一跳。
    package = str(args.get("package") or "")
    return _package_result(_package_report("install", package))


def _handle_package_remove(args: Dict[str, Any], **_kw: Any) -> str:
    package = str(args.get("package") or "")
    return _package_result(_package_report("remove", package))


registry.register(
    name="source_list",
    toolset=TOOLSET,
    schema=SOURCE_LIST_SCHEMA,
    handler=_handle_list,
    emoji="📡",
)
registry.register(
    name="source_read",
    toolset=TOOLSET,
    schema=SOURCE_READ_SCHEMA,
    handler=_handle_read,
    emoji="🔎",
)
registry.register(
    name="source_enable",
    toolset=TOOLSET,
    schema=SOURCE_ENABLE_SCHEMA,
    handler=_handle_enable,
    emoji="🔛",
)
registry.register(
    name="source_set_cadence",
    toolset=TOOLSET,
    schema=SOURCE_SET_CADENCE_SCHEMA,
    handler=_handle_set_cadence,
    emoji="⏱",
)
registry.register(
    name="source_configure",
    toolset=TOOLSET,
    schema=SOURCE_CONFIGURE_SCHEMA,
    handler=_handle_configure,
    emoji="⚙",
)
registry.register(
    name="source_collect",
    toolset=TOOLSET,
    schema=SOURCE_COLLECT_SCHEMA,
    handler=_handle_collect,
    emoji="📥",
)
registry.register(
    name="source_package_list",
    toolset=TOOLSET,
    schema=SOURCE_PACKAGE_LIST_SCHEMA,
    handler=_handle_package_list,
    emoji="📦",
)
registry.register(
    name="source_package_install",
    toolset=TOOLSET,
    schema=SOURCE_PACKAGE_INSTALL_SCHEMA,
    handler=_handle_package_install,
    emoji="⬇️",
)
registry.register(
    name="source_package_remove",
    toolset=TOOLSET,
    schema=SOURCE_PACKAGE_REMOVE_SCHEMA,
    handler=_handle_package_remove,
    emoji="🗑",
)
