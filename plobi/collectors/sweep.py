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


def run_sweep(*, service: Any = None, sources: Optional[tuple[str, ...]] = None,
              apply_mode: Optional[str] = None) -> dict[str, Any]:
    """跑一轮采集。返回逐条读数 + 一句总结；**任何一条都不静默省略**。"""
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

        rows.append(_collect_one(source, collector, service=service, apply_mode=apply_mode))

    ran = [r for r in rows if r["ok"]]
    if not rows:
        verdict = "没有任何一条信源被点亮（`collecting_sources()` 是空的）：这一轮什么都不会做"
    elif not ran:
        verdict = f"{len(rows)} 条一条都没真跑成：" + "、".join(f"{r['source']}（{r['state']}）" for r in rows)
    else:
        verdict = f"{len(ran)} / {len(rows)} 条真跑了一轮：" + "、".join(r["source"] for r in ran)
    return {"sources": rows, "ran": len(ran), "total": len(rows), "verdict": verdict}


def _collect_one(source: str, collector: Any, *, service: Any, apply_mode: Optional[str]) -> dict[str, Any]:
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

    # 契约那一格：只有 pull() 的采集器，落库必须走同一个 ingest，档位不许在这一层猜。
    if apply_mode is None:
        return _row(source, ok=False, state="no_apply_mode",
                    detail="这条只有 `pull()`，落库档位（pending / confirmed）没人给 ⇒ 不落库，不替你猜")
    try:
        occurrences = collector.pull()
    except Exception as exc:                                  # noqa: BLE001
        state = str(getattr(exc, "state", "") or "raised")
        detail = str(getattr(exc, "detail", "") or "") or f"{type(exc).__name__}: {exc}"
        return _row(source, ok=False, state=state, detail=detail)

    from plobi.collectors.ingest import ingest_occurrences

    result = ingest_occurrences(service, occurrences, apply=apply_mode)  # type: ignore[arg-type]
    created = result.get("created", 0) if isinstance(result, dict) else getattr(result, "created", 0)
    return _row(source, ok=True, state="ready", detail=f"拉了 {len(occurrences)} 行，进去 {created} 条新")
