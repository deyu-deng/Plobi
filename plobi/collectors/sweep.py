"""每天那一轮：把注册表说"在采"的信源逐个跑一遍 —— 没有执行方时不许静默。

存在的理由是一条 2026-10-15 现核到的断口：`collecting_sources()` 全仓**零调用方**，
所以「代码合了」与「会在采」中间缺的就是这一层。这一层刻意**不按信源名字分支**（判据 3）：
它只问注册表两件事 —— 这条点没点开关、有没有构造得出它的入口（`SourceSpec.builder`）。

两条落库路线，都不许 fork 存储语义：

1. 采集器自己有 `collect()`（教务那条就是这么写的：自己按配置读 `apply`、自己走
   :func:`plobi.collectors.ingest.ingest_occurrences`）⇒ 直接调它，这一层不替它决定档位。
2. 只有 `pull()`（契约那一格）⇒ 由这里调 :func:`ingest_occurrences`，`apply` 取调用方给的，
   **拿不到就不落库**，报一句"没人在这儿替你猜档位"（判据 7 那颗雷反过来也成立）。

一条信源失败**不许带走其它条**：逐条 try，把状态与那句为什么原样收进读数；
`state` 优先取异常自己带的那一格（`ZjuUnavailable` 就有），取不到才用类名兜，不编原因。
"""

from __future__ import annotations

from typing import Any, Optional


def _row(source: str, *, ok: bool, state: str, detail: str, counts: Optional[dict] = None) -> dict[str, Any]:
    return {"source": source, "ok": ok, "state": state, "detail": detail, **(counts or {})}


def run_sweep(*, service: Any = None, sources: Optional[tuple[str, ...]] = None) -> dict[str, Any]:
    """跑一轮采集。返回逐条读数 + 一句总结；**任何一条都不静默省略**。

    `apply_mode` **已删**（WP-POOL，`specs/source-pool.md`）：「落库档位」是**日程**的
    概念，而这一层现在只往信息池写。留着那个参数就等于还留着一条「直接落日程」的路，
    而那种路是靠自觉守住的——删掉它，这一层连表达那件事的形状都没有。
    """
    from plobi.collectors import registry

    selected = tuple(registry.collecting_sources() if sources is None else sources)
    rows: list[dict[str, Any]] = []

    for source in sorted(selected):
        try:
            spec = registry.spec(source)
        except registry.UnknownSource:
            rows.append(_row(source, ok=False, state="unknown_source",
                             detail="这个名字没在注册表里登记过：它不该被调度，也不该被当成信源"))
            continue

        if spec.builder is None:
            rows.append(_row(source, ok=False, state="no_builder",
                             detail="注册表说这条在采，但没有任何一方构造得出它的采集器 ⇒ 不会有东西进来"))
            continue

        try:
            collector = spec.builder()
        except Exception as exc:                              # noqa: BLE001 - 构造失败也要说得出
            rows.append(_row(source, ok=False, state="build_failed",
                             detail=f"{type(exc).__name__}: {exc}"))
            continue

        rows.append(_collect_one(source, collector, service=service))

    ran = [r for r in rows if r["ok"]]
    if not rows:
        verdict = "没有任何一条信源被点亮（`collecting_sources()` 是空的）：这一轮什么都不会做"
    elif not ran:
        verdict = f"{len(rows)} 条一条都没真跑成：" + "、".join(f"{r['source']}（{r['state']}）" for r in rows)
    else:
        verdict = f"{len(ran)} / {len(rows)} 条真跑了一轮：" + "、".join(r["source"] for r in ran)
    return {"sources": rows, "ran": len(ran), "total": len(rows), "verdict": verdict}


def _collect_one(source: str, collector: Any, *, service: Any) -> dict[str, Any]:
    collect = getattr(collector, "collect", None)
    if callable(collect):
        try:
            result = collect(service=service) if service is not None else collect()
        except Exception as exc:                              # noqa: BLE001 - 状态与话要原样收进读数
            state = str(getattr(exc, "state", "") or "raised")
            detail = str(getattr(exc, "detail", "") or "") or f"{type(exc).__name__}: {exc}"
            return _row(source, ok=False, state=state, detail=detail)
        counts = {k: result.get(k) for k in ("created", "updated", "unchanged", "rows_seen")
                  if isinstance(result, dict)}
        return _row(source, ok=True, state=str(result.get("state") or "ready"),
                    detail=f"进去 {result.get('created', 0)} 条新、{result.get('unchanged', 0)} 条原样",
                    counts=counts)

    # 契约那一格：只有 `pull()` 的采集器，落点是**池**。
    # 旧形状这里有一道 `no_apply_mode` 闸（「档位没人给 ⇒ 不落库」）——**那一格整条删了**：
    # 「落库档位」这个概念属于日程（ADR-0009 的待确认/已确认），而池子不区分那两档。
    # 留着它就等于还留着「日程落库需要一个人给档位」的形状，而池子不需要那个人。
    try:
        occurrences = collector.pull()
    except Exception as exc:                                  # noqa: BLE001
        state = str(getattr(exc, "state", "") or "raised")
        detail = str(getattr(exc, "detail", "") or "") or f"{type(exc).__name__}: {exc}"
        return _row(source, ok=False, state=state, detail=detail)

    # WP-POOL（`specs/source-pool.md`）：`pull()` 那一族**也只写池**。
    # 旧形状在这里 `ingest_occurrences(service, …, apply=apply_mode)` —— 于是任何一条
    # 有 `pull()` 的信源都能直接改日程，而这正是用户 10-19 说的「信息直接去修改日程」。
    #
    # `apply_mode` 这个参数**整个从签名里删了**：留着它就是留着那条路，而「留着但是
    # 不用」靠的是自觉。删掉它，这一层连表达「直接落日程」的形状都没有。
    from plobi.pool import PoolItem
    from plobi.pool import land as pool_land

    result = pool_land([
        PoolItem(
            source=source,
            external_id=occurrence.external_id,
            kind=occurrence.kind or "note",
            title=occurrence.title or "",
            body=occurrence.evidence.get("text", "") if occurrence.evidence else "",
            context=occurrence.evidence or {},
            occurred_at=occurrence.start_at,
        )
        for occurrence in occurrences
    ])
    return _row(source, ok=True, state="ready", detail=f"拉了 {len(occurrences)} 行，进池 {result['created']} 条新")
