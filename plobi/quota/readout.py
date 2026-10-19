"""R-108 的只读口：这条腿**现在能不能用**、**今天花了多少**、**还剩多少**（读不到就照实空着）。

存在的理由是一条现核结论（`Docs/reference/quota-read-surface.md`，2026-10-17）：这台**读得到**
"还能不能用"（网关目录里每条模型自带 `live/live_reason/selectable`）与"花了多少"（客户端会话流水里的
`providerData.rawUsage.credit`），**读不到**"还剩多少"。裁 185 把这一格往前推一步：能读到的那一位
（网关 `/healthz` 账号对象里的 `quota`）真的读出来并带上单位，读不到的那一位照实给 `None` 与原因。
所以这一格的职责不是猜一个余额，而是把这三件事按"有读数 / 没写入方 / 只有界面"分清楚，交出去，
让上层照着它决定"换下一条腿"还是"停工并说明"。**本模块不发任何一次补全**——
全部是文件与网关那两位只读 GET。

三条硬口径（写在类型里，免得调用方各自发挥）：
- `remaining` 只有真读到才给数；给不出就是 `None` + `remaining_reason`，**不许拿估算值冒充**。
- 花费的数必须带 `spent_source`，说清是哪一个真源算出来的；没有真源就是 `None`。
- 每一格都要能追溯到证据（`evidence` 里放 `file:line` 或端点），否则这张表读起来只是形容词。

裁 185 把这一侧收成**一个原子**：`remaining()`——只读、如实。读得到就出数并带**单位**
（钱就是钱，限流窗口的剩余请求数不是钱，两者不许混），读不到就 `None` + 为什么读不到。
`verdict(..., min_usd=...)` 是熔断那条路的消费口：**只有真读到的数才能拦一条腿**，
读不到的腿永远不拦——拿"未知"当"零"就是停工误判，也是他把"成本显示 0"当缺陷的那一类。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from plobi.quota import gateway
from plobi.quota import usage_ledger
from plobi.quota import config as quota_config
from plobi.quota.pool import QuotaPool, get_quota_pool

__all__ = [
    "LegReadout", "BalanceReading", "readout", "remaining", "verdict",
    "credits_today", "fetch_health", "REMAINING_REASONS",
]

# 「读不到还剩多少」的原因——分得清才谈得上下一步该补哪一层。
REASON_NO_WRITER = "no_writer"                      # 槽位在，没有人写（本仓缺的是写入方）
REASON_INTERFACE_UNPROBED = "interface_unprobed"    # 厂商有没有余额接口，这台未实测
REASON_UI_ONLY = "ui_only"                          # 只有界面，本地没有任何可解析的读数
REASON_NOT_REPORTED = "not_reported"                # 写入方在，但这一次那个字段没给值
REASON_NON_NUMERIC = "non_numeric_value"            # 字段有值但不是个数，拒绝替它解释
REASON_READ = "read"                                # 读到了
#: 只有「读不到」的那几种才算原因；`read` 是出了数，不在这个元组里。
REMAINING_REASONS = (
    REASON_NO_WRITER, REASON_INTERFACE_UNPROBED, REASON_UI_ONLY,
    REASON_NOT_REPORTED, REASON_NON_NUMERIC,
)

UNIT_USD = "usd"
UNIT_REQUESTS = "requests"   # 限流窗口剩余请求数：**不是钱**
UNIT_CREDITS = "credits"

#: 网关账号对象 `quota` 里可能出现的字段，按"越像钱越优先"排；命中第一个真读数就停。
#: `remaining` 这一位是 `aigw/aigw/providers/antigravity.py:610-613` 从
#: `x-ratelimit-remaining` 抄进来的**字符串**，语义是这一轮限流窗口还剩几次请求。
GATEWAY_QUOTA_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("usd", UNIT_USD, "美元余额"),
    ("balance_usd", UNIT_USD, "美元余额"),
    ("credits", UNIT_CREDITS, "积分余额"),
    ("remaining", UNIT_REQUESTS, "限流窗口剩余请求数，不是钱"),
)
#: 现核：整台网关里只有这一家有 `quota` 写入方（其余恒空 `{}`）。
GATEWAY_QUOTA_WRITERS = frozenset({"antigravity"})


# 订阅型客户端的会话流水：谁家的积分记在哪个落点。
# 键 = 网关目录里的 provider 名（裁定 46：额度清单只有一份真源，来自目录）。
CREDIT_LOG_ROOTS: dict[str, tuple[str, str]] = {
    # 实测落点：`<root>/projects/**/*.jsonl`，每行一条事件，`timestamp` 是毫秒。
    "workbuddy": ("CODEBUDDY_CONFIG_DIR", ".codebuddy"),
}
CREDIT_FIELD_RE = re.compile(r'"credit"\s*:\s*([0-9]*\.?[0-9]+)')


@dataclass(frozen=True)
class BalanceReading:
    """「还剩多少」的一次读数。`remaining is None` 永远配一个说得清的 `reason`。"""

    name: str
    kind: str
    remaining: Optional[float]
    unit: str
    reason: str
    detail: str
    probe_state: str = "not_read"
    evidence: dict[str, str] = field(default_factory=dict)
    checked_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "remaining": self.remaining,
            "unit": self.unit if self.remaining is not None else "",
            "reason": self.reason,
            "detail": self.detail,
            "probe_state": self.probe_state,
            "evidence": dict(self.evidence),
            "checked_at": self.checked_at,
        }

    @property
    def is_usd(self) -> bool:
        return self.remaining is not None and self.unit == UNIT_USD


def as_number(value: Any) -> Optional[float]:
    """把"人家给的数"变成一个 float；**任何需要解释的值都返回 None**。

    `bool` 被挡在外面是因为 Python 里 `True == 1`——那会把一个状态位读成一块钱。
    NaN/inf、空串、`"null"`、`"unlimited"` 一律不算数；这条是"不许拿估算值冒充"的落点。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def healthz_url(base_url: str) -> str:
    """`/v1` 那个 base URL 去掉尾巴再接 `/healthz`（网关把两位都挂在根上）。"""
    base = (base_url or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/healthz"


@dataclass(frozen=True)
class HealthOutcome:
    """一次 `/healthz` 只读。`accounts` 只在 `state` 是 OK 时才有意义。"""

    state: gateway.CatalogState
    accounts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    detail: str = ""
    checked_at: float = 0.0

    @property
    def answered(self) -> bool:
        return self.state is gateway.CatalogState.OK


def fetch_health(
    base_url: str,
    api_key: str,
    *,
    timeout: float = gateway.DEFAULT_TIMEOUT,
    open_url: Optional[Callable[[Any, float], Any]] = None,
) -> HealthOutcome:
    """读网关 `/healthz` 里每个账号的 `quota`。不发补全，也不抛——读不到就是数据。

    只取四位的账号形状（`id`/`state`/`fail`/`quota`）；`capabilities`、`tokens` 那些
    与"还剩多少"无关，抄进读数表只会把这张表变成日志。
    """
    now = time.time()
    url = healthz_url(base_url)
    if not url:
        return HealthOutcome(gateway.CatalogState.UNREACHABLE, {}, "no gateway base_url configured", now)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        if open_url is not None:
            response = open_url(request, timeout)
        else:
            from plobi_cli.urllib_security import open_credentialed_url

            response = open_credentialed_url(request, timeout=timeout)
        with response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", "?")
        reason = "gateway rejected the key" if code in (401, 403) else f"HTTP {code}"
        return HealthOutcome(gateway.CatalogState.REJECTED, {}, f"{url} {reason}", now)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return HealthOutcome(gateway.CatalogState.UNREACHABLE, {}, f"{url} unreachable ({exc})", now)
    except (ValueError, UnicodeDecodeError) as exc:
        return HealthOutcome(gateway.CatalogState.REJECTED, {}, f"{url} bad JSON ({exc})", now)

    providers = payload.get("providers") if isinstance(payload, dict) else None
    if not isinstance(providers, dict):
        return HealthOutcome(gateway.CatalogState.REJECTED, {}, f"{url} 回体里没有 providers 这一段", now)
    accounts: dict[str, list[dict[str, Any]]] = {}
    for name, info in providers.items():
        rows = (info or {}).get("accounts") if isinstance(info, dict) else None
        if not isinstance(rows, list):
            rows = []
        accounts[str(name).strip().lower()] = [
            {"id": r.get("id"), "state": r.get("state"), "fail": r.get("fail"),
             "quota": r.get("quota") if isinstance(r.get("quota"), dict) else {}}
            for r in rows if isinstance(r, dict)
        ]
    return HealthOutcome(
        gateway.CatalogState.OK, accounts,
        f"{url} 答了，{len(accounts)} 家 provider", now,
    )


def remaining(
    name: str,
    *,
    kind: str = "",
    status: Any = None,
    source: Any = None,
    accounts: Optional[list[dict[str, Any]]] = None,
    probe_state: str = "not_read",
    balance_probe_wired: Optional[bool] = None,
    now: Optional[float] = None,
) -> BalanceReading:
    """**这一侧唯一的原子**：这一条腿还剩多少。读得到出数，读不到 `None` + 原因。

    数只从三个真源取，优先级按"越像钱越优先"，全部是别人已经算好的读数：
    ① 探针回来的 `SourceStatus.remaining_usd`（只有接了 `balance_fn` 才会有）；
    ② 源对象自己的 `remaining_usd()`；
    ③ 网关 `/healthz` 账号对象里的 `quota`（今天整台只有 `antigravity` 有写入方，
       而且那一位数是限流窗口的剩余请求数，单位会照实写成 `requests`）。
    其余一律 `None`，并把"为什么"归到 `REMAINING_REASONS` 里的一格。
    """
    now = time.time() if now is None else now
    ev: dict[str, str] = {}

    usd = as_number(getattr(status, "remaining_usd", None)) if status is not None else None
    if usd is not None:
        ev["remaining"] = "探针带回来的余额（plobi/quota/sources.py:176-202）"
        return BalanceReading(name, kind, usd, UNIT_USD, REASON_READ,
                              "探针真读到过一个美元余额", "probed", ev, now)
    if source is not None:
        try:
            direct = as_number(source.remaining_usd())
        except Exception:  # noqa: BLE001 — 读数取不到就往下走原因，不许把异常当余额
            direct = None
        if direct is not None:
            ev["remaining"] = "源对象 remaining_usd()（B4 seam：None = 未知，不是零）"
            return BalanceReading(name, kind, direct, UNIT_USD, REASON_READ,
                                  "源自己报得出一个美元余额", "probed", ev, now)

    if kind == "aigw":
        rows = list(accounts or [])
        seen_fields: list[str] = []
        # 多家账号**不做合并**：合数是产品决定（求和＝钱包叠得起来吗？取最小＝轮询真会轮到的那家吗？），
        # 在这里发明一种合法就是拿估算值冒充。这一格只交"某一个账号报的那个真数"，并说清是哪一家。
        for field_name, unit, label in GATEWAY_QUOTA_FIELDS:
            present = [(a, a.get("quota") or {}) for a in rows
                        if isinstance(a.get("quota"), dict) and field_name in a["quota"]]
            if not present:
                continue
            seen_fields.append(field_name)
            pairs = [(as_number(q.get(field_name)), a) for a, q in present]
            numbers = [(n, a) for n, a in pairs if n is not None]
            if not numbers:
                ev["non_numeric"] = (f"`quota.{field_name}` 有值但不是个数："
                                     f"{(present[0][1].get(field_name))!r}")
                continue
            number, chosen = min(numbers, key=lambda item: item[0])
            whose = str((chosen or {}).get("id") or "")
            writer = ("｜写入方 aigw/aigw/providers/antigravity.py:610-613，"
                      "取的是响应头 x-ratelimit-remaining" if unit == UNIT_REQUESTS else "")
            ev["remaining"] = ("aigw/aigw/main.py:265-278 的 /healthz → 账号对象 quota."
                               + field_name + (f"（账号 {whose}）" if whose else "") + writer)
            unit_note = label + ("；**熔断按钱决定时不能拿这一位当钱**" if unit == UNIT_REQUESTS else "")
            spread = f"（{len(numbers)} 家报了这一位，交最小的一家 {number}）" if len(numbers) > 1 else ""
            return BalanceReading(name, kind, number, unit, REASON_READ,
                                  f"网关报了 `quota.{field_name}` = {number}{spread}（{unit_note}）",
                                  "answered", ev, now)
        if seen_fields:
            ev["remaining"] = "/healthz 回了 quota 字段，但没有一位是数"
            return BalanceReading(name, kind, None, "", REASON_NON_NUMERIC,
                                  f"`quota` 里出现过 {sorted(set(seen_fields))}，值不是个数，不替它解释",
                                  probe_state, ev, now)
        if name.lower() in GATEWAY_QUOTA_WRITERS:
            ev["remaining"] = "aigw/aigw/providers/antigravity.py:610-613（写入方在，但依赖上游回那个头）"
            return BalanceReading(name, kind, None, "", REASON_NOT_REPORTED,
                                  "这一家有写入方，但这一次账号对象里的 `quota` 是空的"
                                  "（上游没回 `x-ratelimit-remaining`，或网关刚起还没打过一次上游）",
                                  probe_state, ev, now)
        ev["remaining"] = ("aigw/aigw/main.py:277 有 quota 槽位；全仓唯一写入方是 "
                           "aigw/aigw/providers/antigravity.py:610-613")
        return BalanceReading(name, kind, None, "", REASON_NO_WRITER,
                              "网关账号对象里 `quota` 这一位只有 "
                              "`aigw/providers/antigravity.py:610-613` 一处会填（读 `x-ratelimit-remaining`），"
                              f"`{name}` 没有写入方 ⇒ 恒为 `{{}}`",
                              probe_state, ev, now)

    if kind == "cheap_api":
        probe = getattr(source, "balance_probe_wired", None) if source is not None else None
        wired = bool(probe()) if callable(probe) else bool(balance_probe_wired)
        if not wired:
            ev["remaining"] = "plobi/quota/sources.py:150 收 balance_fn、:267 构造时不传"
            return BalanceReading(name, kind, None, "", REASON_INTERFACE_UNPROBED,
                                  "本仓有余额探测槽位但没接线：`balance_probe_wired()` 回 False ⇒ "
                                  "探针只会给 `configured but balance probe not wired`；"
                                  "厂商侧有没有余额端点这台**未实测**，不拿猜测当读数",
                                  probe_state, ev, now)
        ev["remaining"] = "plobi/quota/sources.py:188-192"
        return BalanceReading(name, kind, None, "", REASON_NOT_REPORTED,
                              "余额探测接了线，但这一次没回数（探针没跑过，或厂商回了空）",
                              probe_state, ev, now)

    ev["remaining"] = f"kind={kind or 'unknown'}"
    return BalanceReading(name, kind, None, "", REASON_UI_ONLY,
                          f"这一档（`{kind or 'unknown'}`）本地没有任何可解析的读数落点，"
                          "只有客户端界面看得到剩余额度",
                          probe_state, ev, now)


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
    remaining_unit: str
    remaining_probe_state: str
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
            "remaining_unit": self.remaining_unit,
            "remaining_reason": self.remaining_reason,
            "remaining_detail": self.remaining_detail,
            "remaining_probe_state": self.remaining_probe_state,
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
    health: Optional[Callable[[str, str], HealthOutcome]] = None,
    ledger_rows: Optional[list[dict[str, Any]]] = None,
    credits: Optional[Callable[[str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """一张表：每条腿的可用性 / 今天花费 / 还剩多少（读不到就是空 + 原因）/ 证据。

    `fetch` / `health` / `ledger_rows` / `credits` 是注入位——测试拿假读数跑，
    **一次补全都不发，也不碰真网关**。
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
    if health is None:
        health = lambda url, key: fetch_health(url, key)  # noqa: E731
    outcome = fetch(base_url, api_key) if base_url else gateway.GatewayOutcome(
        gateway.CatalogState.DISABLED, {}, "网关这一档没开或端点读不出（config.gateway_settings）", now
    )
    houtcome = health(base_url, api_key) if base_url else HealthOutcome(
        gateway.CatalogState.DISABLED, {}, "网关这一档没开（config.gateway_settings）", now
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

        reading = remaining(
            name,
            kind=kind,
            status=status,
            source=registered.get(name),
            accounts=houtcome.accounts.get(name.lower()) or [],
            probe_state=houtcome.state.value if base_url else "not_read",
            now=now,
        )

        if kind == "aigw":
            credit = credits(name)
            spent, unit, source_tag = credit["credits"], "credits", "vendor_transcript_credit"
            if spent is None:
                source_tag = ""
            evidence = {"usable": ev,
                        "spent": f"{credit['records']} 条 credit / {credit['files']} 个流水文件；{credit['detail']}",
                        "remaining": reading.evidence.get("remaining", "")}
        else:
            spent, unit = spent_by_source.get(name), "usd"
            source_tag = "spend_ledger_cost" if spent is not None else ""
            evidence = {"usable": ev,
                        "spent": (f"账本里今天有 {priced_by_source.get(name, 0)} 条 priced 行"
                                  if spent is not None else
                                  f"账本今天没有 priced 行（{usage_ledger.COST_STATUS_NO_USAGE} 之类不算数）"),
                        "remaining": reading.evidence.get("remaining", "")}
        for key, value in reading.evidence.items():
            if key != "remaining":
                evidence[f"remaining:{key}"] = value

        legs.append(LegReadout(
            name=name, kind=kind, usable=usable, usable_reason=usable_reason,
            spent_today=spent, spent_unit=unit if spent is not None else "",
            spent_source=source_tag,
            remaining=reading.remaining, remaining_unit=reading.unit,
            remaining_reason=reading.reason, remaining_detail=reading.detail,
            remaining_probe_state=reading.probe_state,
            evidence=evidence,
        ))

    known = [l for l in legs if l.remaining is not None]
    return {
        "checked_at": now,
        "catalog_state": outcome.state.value,
        "catalog_detail": outcome.detail,
        "health_state": houtcome.state.value,
        "health_detail": houtcome.detail,
        "legs": [leg.as_dict() for leg in legs],
        "totals": {
            "spent_today_credits": sum(l.spent_today or 0.0 for l in legs if l.spent_unit == "credits") or None,
            "spent_today_usd": sum(l.spent_today or 0.0 for l in legs if l.spent_unit == "usd") or None,
        },
        # 「这一台读得到剩余额度吗」= 这一次有没有任何一条腿真出了数，不是代码里写死的一位。
        "remaining_readable": bool(known),
        "remaining_known": [
            {"name": l.name, "remaining": l.remaining, "unit": l.remaining_unit} for l in known
        ],
    }

def verdict(report: dict[str, Any], *, min_usd: Optional[float] = None) -> dict[str, Any]:
    """把那张表压成一句可执行的话：能用就报数、有得换就换、一条都不行就停工并说明。

    `min_usd` 是熔断那条路进来的地方（R-108 的"不够就停工"）：**只有真读到的美元数**低于它才拦一条腿；
    `remaining is None` 的腿永远不拦，`requests` 单位的数也永远不拦——那一位是限流窗口的请求数，
    拿它当钱就是"把没读到的东西当结论"。拦完一条都不剩才叫停工。

    这里**只出判断，不改路由**：`pool.resolve()` 现在的口径是"没额度也不硬挡"
    （`plobi/quota/pool.py:221-227`），要不要把它翻成停工是产品决定，不在这一个函数里偷偷改。
    """
    legs = report.get("legs") or []
    up = [l for l in legs if l["usable"] is True]
    unknown = [l for l in legs if l["usable"] is None]
    blocked: list[dict[str, Any]] = []
    if min_usd is not None:
        keep: list[dict[str, Any]] = []
        for leg in up:
            number = leg.get("remaining") if leg.get("remaining_unit") == UNIT_USD else None
            if number is not None and float(number) < float(min_usd):
                blocked.append(leg)
            else:
                keep.append(leg)
        up = keep
    if up:
        action, note = "use", f"{len(up)} 条腿现在能用：{'、'.join(l['name'] for l in up)}"
        if blocked:
            note += "；余额不足先搁下：" + "、".join(
                f"{l['name']}={l['remaining']} {l['remaining_unit']}<{min_usd}" for l in blocked)
    elif blocked:
        action, note = "stop_work", ("能用的腿余额都不够这一档（min_usd="
                                     f"{min_usd}）：" + "、".join(
                                         f"{l['name']} 只剩 {l['remaining']} {l['remaining_unit']}"
                                         for l in blocked))
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
    known_usd = [l for l in legs
                 if l.get("remaining_unit") == UNIT_USD and l.get("remaining") is not None]
    return {
        "action": action,
        "usable": [l["name"] for l in up],
        "unknown": [l["name"] for l in unknown],
        "blocked_by_balance": [l["name"] for l in blocked],
        "sentence": note,
        "remaining_is_known": bool(report.get("remaining_readable")),
        "remaining_known_usd": [f"{l['name']}={l['remaining']}" for l in known_usd],
        "no_estimates": ("剩余额度这台读不到的那些腿一律没参与判断，"
                         "本判断里没有一项是估的"),
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
        description="读「这条腿现在能不能用 + 今天花了多少 + 还剩多少（读不到就说为什么）」",
    )
    ap.add_argument("--sentence", action="store_true", help="只打那一句判断，不打整张表")
    ap.add_argument("--min-usd", type=float, default=None,
                    help="熔断档：真读到的美元余额低于这个数的腿先搁下，一条不剩就停工")
    args = ap.parse_args(argv)

    report = readout()
    decision = verdict(report, min_usd=args.min_usd)
    if args.sentence:
        print(f"{decision['sentence']}（{decision['no_estimates']}）")
    else:
        print(_json.dumps({"report": report, "decision": decision}, ensure_ascii=False, indent=2))
    return exit_code(decision)


if __name__ == "__main__":
    raise SystemExit(main())
