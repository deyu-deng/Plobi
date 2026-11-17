"""边界层：哪一类记忆写入可以自己定（规格 ``specs/memory-consent-learning.md`` §3.3／§3.4，P2）。

三层范式的最后一层。前两层已经各就各位：``consent_events`` 记「人做了什么」，
``preferences`` 把评论长成「这类该怎么写」。这一层回答第三个问题——
**「这类内容，还要不要每次问你？」**——并且把答案写成一条可复算的 trace。

四条硬规矩（规格 §3.3／§3.4／§6／§8 第 3 条，用户 2026-11-16 21:4x 拍定）：

1. **慢晋升、快降级**：同类连续 ``N_ADOPT=5`` 次一致接纳才够格；一次驳回或编辑立刻归零。
2. **硬下限不可越**：日记／画像／删除（``NEVER_AUTO_CATEGORIES`` 与 ``never_auto``）攒多少次、
   有没有政策授权，都判必问。这条在 ``evaluate`` 里**排第一**，后面几条再对也翻不过它。
3. **熔断优先于授权**：``WINDOW_DAYS=7`` 天内全局驳回＋编辑 ≥ ``K_REJECT=3`` ⇒ 暂停**全部**
   自动判定。授权是慢慢挣的，熔断是一次就生效的——成本不对称。
4. **静默必须留痕**：判 ``silent=True`` 的同时写一条 ``silence_traces``；写不进去就按必问办
   （trace 是判据的一部分，不是事后日志）。

判定器分工（用户拍「先量后调」）：这一版**只用规则**。``reviewer=`` 是给将来那个本地否决器
留的位——它**没有放行权**，只在规则已判静默时被问一次，返回 ``{"veto": True}`` 就打回必问；
抛异常／超时／输出不合法一律 fail-closed。**永远不许把规则判「必问」的升级成静默。**

政策（policy）与偏好住同一个正典文件（``preferences.md``，用户拍「单文件、policy 同文件另节」），
条目下面那行 ``<!-- pol:… state:active -->`` 是它生效的证据。候选**永不自动生效**：
``stage_policies`` 只产出一张待批纸，人点头之后才落字。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from plobi.mind.preferences import BOUNDARY_HEADING as POLICY_SECTION
from plobi.mind.preferences import PREFERENCES_LOGICAL, _existing_text

logger = logging.getLogger(__name__)

#: 用户 2026-11-16 21:4x 拍定的三个数（规格 §8 第 3 条）。改这三个数等于改裁定，别在代码里顺手调。
N_ADOPT = 5
K_REJECT = 3
WINDOW_DAYS = 7

DAY = 86400.0

_POLICY_INTRO = """> 每一条都从你的批准史与驳回史里长出来——**你点头之后才生效**。生效的条目意思是
> 「这一类写入不用再问你」；删掉一条＝立刻恢复必问。下面那行 `<!-- pol:… -->` 是它的证据。

"""

#: 偏好条目的证据行（由 ``preferences.Candidate.block`` 写出）。
_EV_RE = re.compile(r"<!--\s*ev:(\S+)\s+category:([^\s]+)\s+action:([^\s]+)\s*-->")
#: 政策条目的证据行（本模块写出）。末尾容得下别的属性（``confidence:…``），
#: 但 ``pol:``／``category:``／``state:`` 三个位置是固定的——别顺手调换顺序。
_POL_RE = re.compile(
    r"<!--\s*pol:(\S+)\s+category:([^\s]+)\s+state:(\S+)(?:\s+evidence:([^\s]*))?[^>]*-->"
)


# ---------------------------------------------------------------- 读数（只给事实）


@dataclass(frozen=True)
class CategoryStats:
    """一类内容的表态史摘要。**只给事实，不给结论**——结论在 :func:`evaluate`。"""

    category: str
    adopted: int = 0
    rejected: int = 0
    edited: int = 0
    streak: int = 0
    streak_source: str = ""      # ``consent_events``（真流水）／``trust_ledger``（迁移前的旧账）
    last_ts: float = 0.0
    recent: tuple[str, ...] = ()  # 最近几条事件 id，给 trace 当证据

    def as_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "adopted": self.adopted,
            "rejected": self.rejected,
            "edited": self.edited,
            "streak": self.streak,
            "streak_source": self.streak_source,
            "last_ts": self.last_ts,
            "recent": list(self.recent),
        }


def _ordered(book, limit: int = 500) -> List[Any]:
    """全部事件按时间**正序**（``recent`` 给的是倒序，streak 要从前往后算）。"""
    return list(reversed(book.recent(max(1, int(limit)))))


def _streak_of(events: Sequence[Any]) -> int:
    """末尾连续接纳数：任何一次驳回或编辑归零（裁定 156／199 的「快降级」）。"""
    run = 0
    for event in events:
        run = run + 1 if str(getattr(event, "action", "")) == "adopted" else 0
    return run


def stats(book, *, now: Optional[float] = None, ledger=None,
          limit: int = 500) -> Dict[str, CategoryStats]:
    """按类摊开：计数 ＋ 当前 streak ＋ 最近证据 id。

    streak **优先取 ``consent.db`` 的真流水**；这一类一条事件都没有时，才回落
    ``TrustLedger`` 的旧账（P1 之前攒下的 streak 不该因为换了账本就从零开始）。
    trace 里会写清 ``streak_source``，不把两个来源混成一个数。
    """
    from plobi.mind import proposal as L1

    stamp = time.time() if now is None else float(now)
    grouped: Dict[str, List[Any]] = {}
    for event in _ordered(book, limit):
        # 政策／偏好正典自己的表态不进业务类别：``category_of`` 把
        # ``Mind/wiki/memory/preferences.md`` 归到 ``wiki``，算进来的话「批准一条政策」
        # 就是给 wiki 加一次 streak——自己给自己发资格。
        # 熔断那边**故意不过滤**：连续驳回政策纸正是「别再自动」的信号。
        if str(getattr(event, "target", "")) == PREFERENCES_LOGICAL:
            continue
        grouped.setdefault(str(getattr(event, "category", "") or "other"), []).append(event)

    book_ledger = ledger if ledger is not None else L1.TrustLedger()
    out: Dict[str, CategoryStats] = {}
    for category, events in grouped.items():
        adopted = sum(1 for e in events if e.action == "adopted")
        rejected = sum(1 for e in events if e.action == "rejected")
        edited = sum(1 for e in events if e.action == "edited")
        out[category] = CategoryStats(
            category=category,
            adopted=adopted,
            rejected=rejected,
            edited=edited,
            streak=_streak_of(events),
            streak_source="consent_events",
            last_ts=max(float(getattr(e, "ts", 0.0) or 0.0) for e in events),
            recent=tuple(str(getattr(e, "id", "")) for e in events[-5:]),
        )

    # 迁移期的旧账：只补**这一类一条事件都没有**的那些格（有流水就以流水为准）。
    for category in L1.NEVER_AUTO_CATEGORIES + ("wiki", "notes", "project-docs", "digests"):
        if category in out:
            continue
        try:
            entry = book_ledger.entry(category, "l1")
        except Exception:  # noqa: BLE001 - 读不到旧账不影响新流水
            continue
        if entry.streak or entry.adopted:
            out[category] = CategoryStats(
                category=category, adopted=entry.adopted, rejected=entry.rejected,
                edited=entry.edited, streak=entry.streak, streak_source="trust_ledger",
                last_ts=0.0, recent=(),
            )
    _ = stamp
    return out


def breaker(book, *, now: Optional[float] = None, window_days: int = WINDOW_DAYS,
            k: int = K_REJECT, limit: int = 500) -> Dict[str, Any]:
    """熔断：窗口内**全局**驳回＋编辑 ≥ K ⇒ 交回人（规格 §3.3）。

    全局而不是按类：熔断要回答的是「这台机器最近是不是在反复判错」，某个类出错说明
    整体判据已经不可信；按类熔断会让「换个类继续自动」变成绕过的口子。
    """
    stamp = time.time() if now is None else float(now)
    floor = stamp - window_days * DAY
    hits = [
        e for e in book.recent(max(1, int(limit)))
        if e.action in ("rejected", "edited") and float(getattr(e, "ts", 0.0) or 0.0) >= floor
    ]
    return {
        "tripped": len(hits) >= k,
        "count": len(hits),
        "threshold": k,
        "window_days": window_days,
        "evidence": [e.id for e in hits[:10]],
        "categories": sorted({e.category for e in hits}),
    }


# ---------------------------------------------------------------- 正典里的条目


def _entries(text: str, pattern: re.Pattern) -> List[Dict[str, Any]]:
    """把 ``- 规则一句话`` ＋ 紧跟的证据行切成条目。**只读文本，不猜语义。**"""
    out: List[Dict[str, Any]] = []
    pending = ""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        match = pattern.search(stripped)
        if match:
            body = pending
            if body.startswith("- "):
                body = body[2:].strip()
            item: Dict[str, Any] = {"text": body, "mark": match.group(0)}
            if pattern is _EV_RE:
                item.update({"evidence": match.group(1), "category": match.group(2),
                             "action": match.group(3), "state": "active"})
            else:
                item.update({"key": match.group(1), "category": match.group(2),
                             "state": match.group(3),
                             "evidence": (match.group(4) or "").split(",")})
            out.append(item)
            pending = ""
            continue
        if stripped.startswith("- "):
            pending = stripped
        elif stripped:
            pending = ""
    return out


def preferences_for(text: str, category: str) -> List[Dict[str, Any]]:
    return [e for e in _entries(text, _EV_RE) if e["category"] == category]


def policies_for(text: str, category: str, *, active_only: bool = False) -> List[Dict[str, Any]]:
    out = [e for e in _entries(text, _POL_RE) if e["category"] == category]
    return [e for e in out if e["state"] == "active"] if active_only else out


def _canon_text(canon: Optional[str]) -> str:
    return _existing_text() if canon is None else str(canon)


# ---------------------------------------------------------------- 判定


@dataclass(frozen=True)
class Verdict:
    """一次「这次要不要问」的判定结果，连带它凭什么。"""

    silent: bool
    reason: str
    category: str
    target: str
    trace: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "silent": self.silent,
            "reason": self.reason,
            "category": self.category,
            "target": self.target,
            "trace": self.trace,
        }


def evaluate(
    book,
    category: str,
    *,
    logical: str = "",
    mode: str = "overwrite",
    canon: Optional[str] = None,
    now: Optional[float] = None,
    ledger=None,
    reviewer: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    record: bool = True,
) -> Verdict:
    """这一次写入可以**不问人**吗？返回 ``Verdict``；``silent=False`` 表示必须先问。

    顺序是有讲究的：硬下限与熔断**排在最前**，因为它们是「拒绝的理由」，不该被后面
    那些「够格的理由」盖掉；ramp 与政策在中间；否决器只在校验已判静默之后才被调用。

    ``record=False`` 只给「只想知道结论、不想落 trace」的读口／测试用——真判静默时
    永远该留痕（规格 §3.4 第⑤条）。
    """
    from plobi.mind import proposal as L1

    stamp = time.time() if now is None else float(now)
    text = _canon_text(canon)
    trace: Dict[str, Any] = {
        "at": stamp,
        "category": str(category or "other"),
        "target": str(logical or ""),
        "mode": str(mode or "overwrite"),
        "rules": [],
        "evidence": [],
        "veto": None,
        # 首版诚实标记：偏好那条是**代理判据**（按类存在已点头条目），不是逐字比对正文。
        "preference_proxy": True,
    }

    def ask(rule: str, reason: str) -> Verdict:
        trace["rules"].append({"rule": rule, "ok": False, "detail": reason})
        trace["decision"] = "ask"
        return Verdict(False, reason, trace["category"], trace["target"], trace)

    # ① 硬下限：永不自动。攒多少、有没有政策，都翻不过这一条。
    why = L1.never_auto(logical, mode) if logical else ""
    if why:
        return ask("never-auto", f"永不自动（{why}）")
    if trace["category"] in L1.NEVER_AUTO_CATEGORIES:
        return ask("never-auto-category", f"类别 {trace['category']} 永不自动（日记／画像这些是"
                                          "「改写过去」，问一次不亏）")
    trace["rules"].append({"rule": "never-auto", "ok": True, "detail": "不在永不自动那几类里"})

    # ② 熔断：一次就生效，压过任何授权。
    br = breaker(book, now=stamp)
    if br["tripped"]:
        trace["breaker"] = br
        return ask("breaker", f"{br['window_days']} 天内驳回／编辑 {br['count']} 次"
                              f"（≥{br['threshold']}）⇒ 全部交回人，等你看过再说")
    trace["breaker"] = br
    trace["rules"].append({"rule": "breaker", "ok": True,
                           "detail": f"窗口内 {br['count']}/{br['threshold']} 次驳回"})

    # ③ 慢晋升：同类连续接纳够不够。
    by_category = stats(book, now=stamp, ledger=ledger).get(trace["category"])
    if by_category is None:
        return ask("ramp", f"类别 {trace['category']} 还没有表态史（连续采纳 0/{N_ADOPT}）")
    trace["stats"] = by_category.as_dict()
    trace["evidence"] = list(by_category.recent)
    if by_category.streak < N_ADOPT:
        return ask("ramp", f"类别 {trace['category']} 连续采纳 {by_category.streak}/{N_ADOPT}，还没攒够")
    trace["rules"].append({"rule": "ramp", "ok": True,
                           "detail": f"连续采纳 {by_category.streak}/{N_ADOPT}"
                                     f"（来源 {by_category.streak_source}）"})

    # ④ 偏好：这一类有没有人点头过的写法约定。首版是**存在性代理**，不当语义比对。
    prefs = preferences_for(text, trace["category"])
    trace["preferences"] = [p["text"] for p in prefs]
    trace["rules"].append({
        "rule": "preference", "ok": True,
        "detail": (f"这一类有 {len(prefs)} 条已点头偏好（代理判据：查存在，不逐字比正文）"
                   if prefs else "这一类还没有偏好条目 ⇒ 无约可违，视作空真"),
    })

    # ⑤ 政策授权：真正的前置。没有它，ramp 攒够也不静默。
    active = policies_for(text, trace["category"], active_only=True)
    trace["policies"] = [p["text"] for p in active]
    # 给「事后留话」用的机器键：写完之后要能说清「凭的是哪一条政策」，
    # 光有一句人读文本对不回去。
    trace["policy_keys"] = [str(p.get("key") or "") for p in active]
    if not active:
        return ask("policy", f"类别 {trace['category']} 还没有你点头过的政策（攒够了也要先授权）")
    trace["rules"].append({"rule": "policy", "ok": True,
                           "detail": f"{len(active)} 条已生效政策"})
    for policy in active:
        trace["evidence"].extend([e for e in policy.get("evidence", []) if e])

    # ⑥ 规则全过 ⇒ 候选静默。到这里才允许问否决器，它只许收紧。
    provisional = {"silent": True, "category": trace["category"],
                   "target": trace["target"], "trace": trace}
    if reviewer is not None:
        try:
            out = reviewer(provisional)
            veto = bool((out or {}).get("veto"))
        except Exception as exc:  # noqa: BLE001 - 否决器不可用 ⇒ 必问（fail-closed）
            trace["veto"] = {"error": f"{type(exc).__name__}: {exc}"}
            return ask("reviewer", "否决器不可用（fail-closed，按必问办）")
        trace["veto"] = {"veto": veto, "reason": str((out or {}).get("reason") or "")}
        if veto:
            return ask("reviewer-veto",
                       str((out or {}).get("reason") or "否决器打回了这一次静默"))

    trace["decision"] = "silent"
    verdict = Verdict(True, "够格静默（硬下限／熔断／ramp／偏好／政策五条全过）",
                      trace["category"], trace["target"], trace)
    if record:
        try:
            book.record_trace(verdict.category, silent=True, reason=verdict.reason,
                              target=verdict.target, trace=trace, ts=stamp)
        except Exception as exc:  # noqa: BLE001 - 留不下痕的静默不算静默
            trace["rules"].append({"rule": "trace", "ok": False,
                                   "detail": f"{type(exc).__name__}: {exc}"})
            trace["decision"] = "ask"
            return Verdict(False, "静默判据写不进流水 ⇒ 按必问办", verdict.category,
                           verdict.target, trace)
    return verdict


# ---------------------------------------------------------------- 政策候选


@dataclass(frozen=True)
class PolicyCandidate:
    kind: str          # ``pause``（曾经出错 ⇒ 回到必问）／``promote``（攒够了 ⇒ 可以不问）
    category: str
    text: str
    evidence: tuple[str, ...]
    confidence: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.category}"

    def block(self) -> str:
        ev = ",".join(self.evidence)
        return (
            f"- {self.text}\n"
            f"  <!-- pol:{self.key} category:{self.category} state:proposed"
            f" evidence:{ev} confidence:{self.confidence} -->\n"
        )


def policy_candidates(book, *, now: Optional[float] = None, existing: Optional[str] = None,
                      window_days: int = WINDOW_DAYS) -> List[PolicyCandidate]:
    """从表态史归纳政策候选（**模板生成，零模型**）。

    两个方向，都只产候选：

    - ``pause``：窗口内同类驳回／编辑 ≥2 ⇒ 「这类先一律问」。什么时候都能出。
    - ``promote``：同类 streak ≥ N 且该类还没有生效政策 ⇒ 「这类可以不问」。只在本刀
      判据通过 ramp 之后才提，免得在还没攒够时就去骚扰人。
    """
    from plobi.mind import proposal as L1

    stamp = time.time() if now is None else float(now)
    floor = stamp - window_days * DAY
    text = _canon_text(existing)
    orders = _ordered(book)

    out: List[PolicyCandidate] = []
    per_category: Dict[str, List[Any]] = {}
    for event in orders:
        # 政策／偏好正典自己的批准史**不算任何业务类别的表态**：``category_of`` 把
        # ``Mind/wiki/memory/preferences.md`` 归到 ``wiki``，不过滤的话「批准一条政策」
        # 等于给 wiki 加一次 streak——自己给自己发资格，晋升就成了自证。
        if str(getattr(event, "target", "")) == PREFERENCES_LOGICAL:
            continue
        per_category.setdefault(str(event.category or "other"), []).append(event)

    for category, events in per_category.items():
        if category in L1.NEVER_AUTO_CATEGORIES:
            continue
        recent_bad = [e for e in events
                      if e.action in ("rejected", "edited") and float(e.ts or 0.0) >= floor]
        has_active = bool(policies_for(text, category, active_only=True))
        streak = _streak_of(events)
        if len(recent_bad) >= 2:
            out.append(PolicyCandidate(
                kind="pause", category=category,
                text=(f"类别 `{category}` 的写入：最近 {window_days} 天被驳回／改写 "
                      f"{len(recent_bad)} 次 ⇒ 一律先问，不自动放行。"),
                evidence=tuple(e.id for e in recent_bad[:5]),
                confidence=f"{len(recent_bad)} 次驳回 / {sum(1 for e in events if e.action == 'adopted')} 次采纳",
            ))
            continue
        if streak >= N_ADOPT and not has_active:
            adopted = [e for e in events if e.action == "adopted"][-N_ADOPT:]
            out.append(PolicyCandidate(
                kind="promote", category=category,
                text=(f"类别 `{category}` 的写入：你连着点头 {streak} 次、一次驳回都没有 "
                      f"⇒ 以后这类我不再问，直接写；驳回一次就恢复必问。"),
                evidence=tuple(e.id for e in adopted),
                confidence=f"{streak} 次一致采纳 / 0 次驳回",
            ))
    out.sort(key=lambda c: (c.kind, c.category))
    return out


def pending_policies(book, *, existing: Optional[str] = None, now: Optional[float] = None,
                     window_days: int = WINDOW_DAYS) -> List[PolicyCandidate]:
    """还没问过的候选。

    两条幂等：① ``kind:category`` 已经进了正典；② 窗口内**问过**（``policy_notes``
    里有 ``staged``／``dismissed``）。只看正典是不够的——一张纸被驳回后会被
    ``discard_pending`` 删掉，正典里从头到尾没有它，于是下一轮又原样再问一次。
    """
    text = _canon_text(existing)
    seen = {e["key"] for e in _entries(text, _POL_RE)}
    stamp = time.time() if now is None else float(now)
    floor = stamp - window_days * DAY
    try:
        asked = book.policy_notes(since=floor)
    except Exception as exc:  # noqa: BLE001 - 备注表读不出来不该阻断归纳
        logger.warning("mind: policy notes unreadable (%s); staging may repeat", exc)
        asked = {}
    return [c for c in policy_candidates(book, now=now, existing=text, window_days=window_days)
            if c.key not in seen and c.key not in asked]


def render(existing: str, fresh: Sequence[PolicyCandidate]) -> str:
    """把候选接在 ``## 边界政策`` 那一节末尾；节不存在就先立节。"""
    base = existing if str(existing or "").strip() else _existing_text()
    if not str(base).strip():
        from plobi.mind.preferences import _HEADER

        base = _HEADER
    if POLICY_SECTION not in base:
        base = base.rstrip("\n") + "\n\n" + POLICY_SECTION + "\n\n" + _POLICY_INTRO
    elif not base.endswith("\n"):
        base += "\n"
    block = "".join(c.block() for c in fresh)
    if base and not base.endswith("\n\n") and not base.endswith("\n"):
        base += "\n"
    return base + block


def stage_policies(book, *, writer=None, existing: Optional[str] = None,
                   now: Optional[float] = None):
    """把待点头的政策候选写成一张计划。**返回 ``WriteResult``；正典此刻一个字不变。**"""
    fresh = pending_policies(book, existing=existing, now=now)
    if not fresh:
        return None
    if writer is None:
        from plobi.mind.writer import MindWriter

        writer = MindWriter(require_approval=True)
    result = writer.write_one(PREFERENCES_LOGICAL, render(_canon_text(existing), fresh))
    if getattr(result, "staged", ""):
        # 真的落了一张纸 ⇒ 记一笔「问过」。记不进去不算失败：下次顶多再问一次。
        stamp = time.time() if now is None else float(now)
        for candidate in fresh:
            try:
                book.note_policy(candidate.key, "staged", ts=stamp)
            except Exception as exc:  # noqa: BLE001 - 备注失败不改变本轮结果
                logger.warning("mind: could not note staged policy %s: %s", candidate.key, exc)
    return result


#: 递归闸。``stage_policies`` 里那次 ``write_one`` 会再进 ``MindWriter.write()``，
#: 而 ``write()`` 又要调 :func:`maybe_stage_policies`——不挡就是无限递归。
_STAGING = threading.local()


def maybe_stage_policies(book=None, *, now: Optional[float] = None):
    """写入路径上的归纳口：有新候选就提一张纸，没有就什么都不做。

    挂在这条腿上而不是「每日摘要」：后者在 ``plobi/mind/provider.py:675`` 已被注释
    （用户 2026-10-29「每日摘要是干什么用的。我都没有定义过」），真正的写入腿是
    ``provider.on_memory_write``。**任何异常都不许冒泡**——归纳失败顶多少问一次，
    不能让一次写入因为归纳而失败。
    """
    if getattr(_STAGING, "active", False):
        return None
    _STAGING.active = True
    try:
        if book is None:
            from plobi.mind.consent_events import ConsentLedger

            book = ConsentLedger()
        return stage_policies(book, now=now)
    except Exception as exc:  # noqa: BLE001 - 归纳不是写入的必要条件
        logger.warning("mind: policy staging skipped (%s: %s)", type(exc).__name__, exc)
        return None
    finally:
        _STAGING.active = False


def approve(pending_id: str) -> str:
    """点头一张政策纸——走**同一处**批准实现，不另写一遍。"""
    from plobi_cli.write_approval_commands import handle_pending_subcommand

    return handle_pending_subcommand("memory", ["approve", pending_id])


# ---------------------------------------------------------------- 读口


def snapshot(book, *, now: Optional[float] = None, canon: Optional[str] = None,
             limit: int = 50) -> Dict[str, Any]:
    """屏幕要的那一份：每类读数 ＋ 熔断 ＋ 已生效政策 ＋ 最近 trace。**只读**。"""
    text = _canon_text(canon)
    rows = stats(book, now=now)
    policies = [dict(e, category=e["category"]) for e in _entries(text, _POL_RE)]
    return {
        "categories": {k: v.as_dict() for k, v in sorted(rows.items())},
        "breaker": breaker(book, now=now),
        "policies": policies,
        "traces": book.recent_traces(max(1, min(int(limit), 200))),
        "thresholds": {"n_adopt": N_ADOPT, "k_reject": K_REJECT, "window_days": WINDOW_DAYS},
        "canon": PREFERENCES_LOGICAL,
        "db": str(book.path),
    }
