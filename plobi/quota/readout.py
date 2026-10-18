"""R-108 的只读口：这条腿**现在能不能用**、**今天花了多少**；「还剩多少」读不到就照实空着。

存在的理由是一条现核结论（`Docs/reference/quota-read-surface.md`，2026-10-17）：这台**读得到**
"还能不能用"（网关目录里每条模型自带 `live/live_reason/selectable`）与"花了多少"（客户端会话流水里的
`providerData.rawUsage.credit`），**读不到**"还剩多少"。所以这一格的职责不是猜一个余额，
而是把这三件事按"有读数 / 没写入方 / 只有界面"分清楚，交出去，让上层照着它决定
"换下一条腿"还是"停工并说明"。**本模块不发任何一次补全**——全部是文件与一次只读的目录请求。

三条硬口径（写在类型里，免得调用方各自发挥）：
- `remaining` 只有真读到才给数；给不出就是 `None` + `remaining_reason`，**不许拿估算值冒充**。
- 花费的数必须带 `spent_source`，说清是哪一个真源算出来的；没有真源就是 `None`。
- 每一格都要能追溯到证据（`evidence` 里放 `file:line` 或端点），否则这张表读起来只是形容词。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from plobi.quota import gateway
from plobi.quota import usage_ledger
from plobi.quota import config as quota_config
from plobi.quota.pool import QuotaPool, get_quota_pool

__all__ = ["LegReadout", "readout", "verdict", "credits_today", "REMAINING_REASONS"]

# 「读不到还剩多少」的三种原因——分得清才谈得上下一步该补哪一层。
REASON_NO_WRITER = "no_writer"                      # 槽位在，没有人写（本仓缺的是写入方）
REASON_INTERFACE_UNPROBED = "interface_unprobed"    # 厂商有没有余额接口，这台未实测
REASON_UI_ONLY = "ui_only"                          # 只有界面，本地没有任何可解析的读数
REMAINING_REASONS = (REASON_NO_WRITER, REASON_INTERFACE_UNPROBED, REASON_UI_ONLY)

# 订阅型客户端的会话流水：谁家的积分记在哪个落点。
# 键 = 网关目录里的 provider 名（裁定 46：额度清单只有一份真源，来自目录）。
CREDIT_LOG_ROOTS: dict[str, tuple[str, str]] = {
    # 实测落点：`<root>/projects/**/*.jsonl`，每行一条事件，`timestamp` 是毫秒。
    "workbuddy": ("CODEBUDDY_CONFIG_DIR", ".codebuddy"),
}
CREDIT_FIELD_RE = re.compile(r'"credit"\s*:\s*([0-9]*\.?[0-9]+)')


@dataclass(frozen=True)
class LegReadout:
    """一条腿的一次读数。所有 `None` 都是"没读到"，不是"没有"。"""

    name: str
    kind: str
    usable: Optional[bool]
    usable_reason: str
    spent_today: Optional[float]
    spent_unit: str
    spent_source: str
    remaining: Optional[float]
    remaining_reason: str
    remaining_detail: str
    evidence: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "usable": self.usable,
            "usable_reason": self.usable_reason,
            "spent_today": self.spent_today,
            "spent_unit": self.spent_unit,
            "spent_source": self.spent_source,
            "remaining": self.remaining,
            "remaining_reason": self.remaining_reason,
            "remaining_detail": self.remaining_detail,
            "evidence": dict(self.evidence),
        }


def _today_start(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _config_dir(env: dict[str, str], var: str, fallback_tail: str) -> Optional[Path]:
    """客户端自己的配置落点：env 优先，回退 `~/<tail>`（不硬编盘符）。"""
    raw = (env.get(var) or os.environ.get(var) or "").strip()
    if raw:
        return Path(raw)
    home = env.get("HOME") or os.environ.get("USERPROFILE") or ""
    if not home:
        return None
    return Path(home) / fallback_tail


def credits_today(
    provider: str,
    *,
    now: Optional[float] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """今天在这条订阅腿上真花掉的积分（厂商会话流水里的 `providerData.rawUsage.credit`）。

    只读。返回的形状里 `credits` 给不出就是 `None`，并说清为什么给不出。
    文件按 mtime 预筛（append-only 的流水，今天没动过的文件不可能有今天的行），
    但**判定用行内 `timestamp`**，mtime 只用来少开文件——时钟跳过也只可能多扫，不会漏算已写入的行。
    """
    env = env or {}
    now = time.time() if now is None else now
    out: dict[str, Any] = {
        "provider": provider,
        "credits": None,
        "records": 0,
        "files": 0,
        "skipped_no_timestamp": 0,
        "detail": "",
    }
    entry = CREDIT_LOG_ROOTS.get(provider)
    if entry is None:
        out["detail"] = f"没有为 `{provider}` 登记过流水落点，不知道去哪读它的积分"
        return out
    root = _config_dir(env, entry[0], entry[1])
    if root is None or not root.is_dir():
        out["detail"] = f"落点读不到：{entry[0]} 未设且默认目录不存在（{entry[1]}）"
        return out
    projects = root / "projects"
    if not projects.is_dir():
        out["detail"] = f"{projects} 不在，读不到会话流水"
        return out

    start = _today_start(now)
    # 预筛给一天宽限：本台时钟会跳，mtime 只当"可能没今天的东西"的粗筛。
    stamp = start - 86_400.0
    total, records, scanned, skipped = 0.0, 0, 0, 0
    for path in sorted(projects.glob("*/*.jsonl")):
        try:
            if path.stat().st_mtime < stamp:
                continue
        except OSError:
            continue
        scanned += 1
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"credit"' not in line:
                    continue
                found = [float(x) for x in CREDIT_FIELD_RE.findall(line)]
                if not found:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = row.get("timestamp")
                try:
                    at = float(ts) / 1000.0
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                if start <= at <= now + 1.0:
                    total += sum(found)
                    records += 1
    out.update({"credits": round(total, 2), "records": records, "files": scanned,
                "skipped_no_timestamp": skipped, "detail": "按行内 timestamp 取今天零点到现在"})
    return out


def _usable_from_catalog(name: str, source: dict[str, Any]) -> tuple[Optional[bool], str, str]:
    """「能不能用」直接取网关目录里已经算好的那几位，不自己判字节。"""
    avail = source.get("availability") or {}
    if avail.get("selectable") is True:
        return True, "catalog_selectable", f"provider={name} live={avail.get('live')} reason={avail.get('live_reason')}"
    if avail:
        return False, str(avail.get("unselectable_reason") or avail.get("live_reason") or "not_selectable"), \
            f"provider={name} live={avail.get('live')} selectable={avail.get('selectable')}"
    return None, "no_availability", f"provider={name} 的目录行里没有可用性字段"


def readout(
    *,
    pool: Optional[QuotaPool] = None,
    now: Optional[float] = None,
    env: Optional[dict[str, str]] = None,
    fetch: Optional[Callable[[str, str], gateway.GatewayOutcome]] = None,
    ledger_rows: Optional[list[dict[str, Any]]] = None,
    credits: Optional[Callable[[str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """一张表：每条腿的可用性 / 今天花费 / 剩余（读不到就是空）/ 证据。

    `fetch` / `ledger_rows` / `credits` 是注入位——测试拿假读数跑，**不碰真目录也不烧额度**。
    """
    pool = pool or get_quota_pool()
    now = time.time() if now is None else now
    env = env if env is not None else {}
    credits = credits or (lambda provider: credits_today(provider, now=now, env=env))

    settings = quota_config.gateway_settings()
    base_url = str(settings.get("base_url") or "") if settings.get("enabled") else ""
    api_key = str(settings.get("api_key") or "")
    if fetch is None:
        fetch = lambda url, key: gateway.fetch_sources(url, key)  # noqa: E731
    outcome = fetch(base_url, api_key) if base_url else gateway.GatewayOutcome(
        gateway.CatalogState.DISABLED, {}, "网关这一档没开或端点读不出（config.gateway_settings）", now
    )

    if ledger_rows is None:
        ledger_rows = usage_ledger.read_recent(limit=2000)
    start = _today_start(now)
    spent_by_source: dict[str, float] = {}
    priced_by_source: dict[str, int] = {}
    for row in ledger_rows:
        if not row.get("ok") or row.get("cost") is None:
            continue
        if float(row.get("ts") or 0.0) < start:
            continue
        name = str(row.get("source") or "")
        spent_by_source[name] = spent_by_source.get(name, 0.0) + float(row["cost"])
        priced_by_source[name] = priced_by_source.get(name, 0) + 1

    statuses = {s.name: s for s in pool.statuses()}
    registered = getattr(pool, "sources", {}) or {}
    legs: list[LegReadout] = []
    names = sorted(set(list(pool.names()) + list(outcome.sources) + list(statuses) + list(registered)))
    for name in names:
        derived = outcome.sources.get(name) or {}
        status = statuses.get(name)
        kind = str(derived.get("kind")
                   or (status.kind if status else "")
                   or (getattr(registered.get(name), "kind", "") if name in registered else "")) or "unknown"

        if derived:
            usable, usable_reason, ev = _usable_from_catalog(name, derived)
        elif status is not None:
            usable, usable_reason = status.usable, f"{status.health.value}: {status.detail}"
            ev = f"pool 里那档探针的读数（{status.checked_at:.0f}）"
        else:
            usable, usable_reason, ev = None, "not_probed", "没有探针读数，也没有目录行"

        if kind == "aigw":
            credit = credits(name)
            spent, unit, source_tag = credit["credits"], "credits", "vendor_transcript_credit"
            if spent is None:
                source_tag = ""
            remaining_reason, remaining_detail = REASON_NO_WRITER, (
                "网关账号对象里的 `quota` 只有 "
                "`aigw/providers/antigravity.py:610-613` 那一处会填（读 `x-ratelimit-remaining`），"
                "这一家没有写入方；`/healthz` 现读 `quota={}`"
            )
            evidence = {"usable": ev,
                        "spent": f"{credit['records']} 条 credit / {credit['files']} 个流水文件；{credit['detail']}",
                        "remaining": "aigw/main.py:277 有槽位，workbuddy provider 无写入方"}
        else:
            spent, unit = spent_by_source.get(name), "usd"
            source_tag = "spend_ledger_cost" if spent is not None else ""
            remaining_reason, remaining_detail = REASON_INTERFACE_UNPROBED, (
                "本仓有探测槽位但没实现：`plobi/quota/sources.py:142` 收 `balance_fn`，"
                "`:245-256` 构造时不传 ⇒ `probe_sources()` 现读 `configured but balance probe not wired`；"
                "厂商侧有没有余额端点这台**未实测**，不拿猜测当读数"
            )
            evidence = {"usable": ev,
                        "spent": (f"账本里今天有 {priced_by_source.get(name, 0)} 条 priced 行"
                                  if spent is not None else
                                  f"账本今天没有 priced 行（{usage_ledger.COST_STATUS_NO_USAGE} 之类不算数）"),
                        "remaining": remaining_detail}

        legs.append(LegReadout(
            name=name, kind=kind, usable=usable, usable_reason=usable_reason,
            spent_today=spent, spent_unit=unit if spent is not None else "",
            spent_source=source_tag, remaining=None,
            remaining_reason=remaining_reason, remaining_detail=remaining_detail,
            evidence=evidence,
        ))

    return {
        "checked_at": now,
        "catalog_state": outcome.state.value,
        "catalog_detail": outcome.detail,
        "legs": [leg.as_dict() for leg in legs],
        "totals": {
            "spent_today_credits": sum(l.spent_today or 0.0 for l in legs if l.spent_unit == "credits") or None,
            "spent_today_usd": sum(l.spent_today or 0.0 for l in legs if l.spent_unit == "usd") or None,
        },
        "remaining_readable": False,
    }

def verdict(report: dict[str, Any]) -> dict[str, Any]:
    """把那张表压成一句可执行的话：能用就报数、有得换就换、一条都不行就停工并说明。

    这里**只出判断，不改路由**：`pool.resolve()` 现在的口径是"没额度也不硬挡"
    （`plobi/quota/pool.py:221-227`），要不要把它翻成停工是产品决定，不在这一个函数里偷偷改。
    """
    legs = report.get("legs") or []
    up = [l for l in legs if l["usable"] is True]
    unknown = [l for l in legs if l["usable"] is None]
    if up:
        action, note = "use", f"{len(up)} 条腿现在能用：{'、'.join(l['name'] for l in up)}"
    elif report.get("catalog_state") in {gateway.CatalogState.UNREACHABLE.value,
                                         gateway.CatalogState.REJECTED.value,
                                         gateway.CatalogState.DISABLED.value}:
        # 没读数 ≠ 读到了"都不能用"。这一档必须停在"再探一次"，不许冒充停工的判断依据。
        action, note = "probe_again", (f"没一条腿确认能用，也没一条确认不能用——网关这次读不到："
                                       f"{report.get('catalog_detail')}")
    elif not legs:
        action, note = "stop_work", "一条腿都没有：网关目录回得空，没有任何可花出去的额度"
    else:
        reasons = "、".join(f"{l['name']}({l['usable_reason']})" for l in legs)
        action, note = "stop_work", f"所有腿都不能用：{reasons}"
    return {
        "action": action,
        "usable": [l["name"] for l in up],
        "unknown": [l["name"] for l in unknown],
        "sentence": note,
        "remaining_is_known": bool(report.get("remaining_readable")),
        "no_estimates": "剩余额度这台读不到，本判断里没有一项是估的",
    }


def exit_code(decision: dict[str, Any]) -> int:
    """把判断压成一个退出码，好让定时作业与 L2 用 `terminal` 直接读，不必新增核心工具。

    0 = 有腿可用；1 = 这次没读数（该再探，不该据此停工）；2 = 确认一条腿都不能用（停工那一档）。
    """
    return {"use": 0, "probe_again": 1, "stop_work": 2}.get(str(decision.get("action")), 1)


def main(argv: Optional[list[str]] = None) -> int:
    """`python -m plobi.quota.readout` —— 只读地打印那张表，一次补全都不发。"""
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        prog="python -m plobi.quota.readout",
        description="读「这条腿现在能不能用 + 今天花了多少」；剩余额度这台读不到就照实空着",
    )
    ap.add_argument("--sentence", action="store_true", help="只打那一句判断，不打整张表")
    args = ap.parse_args(argv)

    report = readout()
    decision = verdict(report)
    if args.sentence:
        print(f"{decision['sentence']}（{decision['no_estimates']}）")
    else:
        print(_json.dumps({"report": report, "decision": decision}, ensure_ascii=False, indent=2))
    return exit_code(decision)


if __name__ == "__main__":
    raise SystemExit(main())
