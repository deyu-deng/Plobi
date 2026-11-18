"""每天那一轮：把注册表说"在采"的信源逐个跑一遍 —— 没有执行方时不许静默。

存在的理由是一条 2026-10-15 现核到的断口：`collecting_sources()` 全仓**零调用方**，
所以「代码合了」与「会在采」中间缺的就是这一层。这一层刻意**不按信源名字分支**（判据 3）：
它只问注册表两件事 —— 这条点没点开关、有没有构造得出它的入口（`SourceSpec.builder`）。

**落池只有一处**（2026-11-17 用户拍「落点收成一处」）：两条触发路线（腿自己有 `collect()`
／腿只有 `pull()`）**都**经 :func:`plobi.collectors.base.land_occurrences` 写池。
从前这两条各写各的（微信/教务/浙大自己调 `pool.land`，其余走这里内联的那份 `PoolItem`），
而分岔点是**运行时探测**出来的隐藏协议 ⇒ 落池语义取决于你抄了谁。现在只剩**触发**的差别，
没有**落点**的差别。

一条信源失败**不许带走其它条**：逐条 try，把状态与那句为什么原样收进读数；
`state` 优先取异常自己带的那一格（`ZjuUnavailable` 就有），取不到才用类名兜，不编原因。
"""

from __future__ import annotations

from typing import Any, Optional


def _row(source: str, *, ok: bool, state: str, detail: str, counts: Optional[dict] = None) -> dict[str, Any]:
    return {"source": source, "ok": ok, "state": state, "detail": detail, **(counts or {})}


def run_sweep(
    *,
    service: Any = None,
    sources: Optional[tuple[str, ...]] = None,
    params: Optional[dict[str, Any]] = None,
    collectors: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """跑一轮采集。返回逐条读数 + 一句总结；**任何一条都不静默省略**。

    `apply_mode` **已删**（WP-POOL，`specs/source-pool.md`）：「落库档位」是**日程**的
    概念，而这一层现在只往信息池写。留着那个参数就等于还留着一条「直接落日程」的路，
    而那种路是靠自觉守住的——删掉它，这一层连表达那件事的形状都没有。

    `params` 是**透传**给腿的参数字典（键由腿自己认，公共层不解释语义）。它是那几条
    私有触发路径把自己那一格参数递进来的唯一通道——公共层**不按信源名字分支**，
    所以「微信要回看几天」这件事只能由触发方自己递进来。

    `collectors` 是**注入缝**：给了就用给的采集器，不走 `spec.builder()`。
    微信那几条路径靠它保住「一个进程只有一份 `ChatlogPipeline`」这条不变量
    （会话名单／准入状态／seen 账都在那一份上），也保住测试能塞假腿进去。
    """
    from plobi.collectors import registry

    selected = tuple(registry.collecting_sources() if sources is None else sources)
    extra = dict(params or {})
    injected = dict(collectors or {})
    rows: list[dict[str, Any]] = []

    for source in sorted(selected):
        collector = injected.get(source)
        if collector is None:
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

        rows.append(_collect_one(source, collector, service=service, params=extra))

    ran = [r for r in rows if r["ok"]]
    if not rows:
        verdict = "没有任何一条信源被点亮（`collecting_sources()` 是空的）：这一轮什么都不会做"
    elif not ran:
        verdict = f"{len(rows)} 条一条都没真跑成：" + "、".join(f"{r['source']}（{r['state']}）" for r in rows)
    else:
        verdict = f"{len(ran)} / {len(rows)} 条真跑了一轮：" + "、".join(r["source"] for r in ran)
    return {"sources": rows, "ran": len(ran), "total": len(rows), "verdict": verdict}


def run_source(
    source_id: str,
    *,
    service: Any = None,
    params: Optional[dict[str, Any]] = None,
    collectors: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """**单腿触发**：走 :func:`run_sweep` 那一条公共触发面，取回这一腿自己的读数。

    为什么有这一层而不是让每个调用方自己去 `run_sweep` 里翻那一行：翻法只有一种，
    抄成四份就有四种（前端那几条路径已经各抄过一次）。这里把「找到我那一行」收成一处。

    **不算第二个触发面**：它自己不碰采集器，只是 `run_sweep` 的一个薄壳。
    """
    report = run_sweep(service=service, sources=(source_id,), params=params, collectors=collectors)
    rows = report.get("sources") or []
    row = rows[0] if rows else {}
    return {
        "source": source_id,
        "ok": bool(row.get("ok")),
        "state": str(row.get("state") or ""),
        "detail": str(row.get("detail") or ""),
        # 那几个计数在**行的那一层**（`_row` 把它们摊开了，`source_tools` 读的就是那一层）。
        "counts": {k: row.get(k) for k in ("created", "updated", "unchanged", "rows_seen") if k in row},
        #: 腿自己那份原始读数（微信那条的逐条 `created`／`updated` 列表与媒体那几格在这里）。
        "report": dict(row.get("report") or {}),
    }


def _collect_one(
    source: str,
    collector: Any,
    *,
    service: Any,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    extra = dict(params or {})
    collect = getattr(collector, "collect", None)
    if callable(collect):
        try:
            if service is not None:
                result = collect(service=service, **extra)
            else:
                result = collect(**extra)
        except Exception as exc:                              # noqa: BLE001 - 状态与话要原样收进读数
            state = str(getattr(exc, "state", "") or "raised")
            detail = str(getattr(exc, "detail", "") or "") or f"{type(exc).__name__}: {exc}"
            return _row(source, ok=False, state=state, detail=detail)
        counts = {k: result.get(k) for k in ("created", "updated", "unchanged", "rows_seen")
                  if isinstance(result, dict)}
        row = _row(source, ok=True, state=str(result.get("state") or "ready"),
                   detail=f"进去 {result.get('created', 0)} 条新、{result.get('unchanged', 0)} 条原样",
                   counts=counts)
        # 腿自己那份读数**原样带上**：微信那几条路径要拿它那几格逐条的
        # `created`／`updated`（通知与读数都靠它）——不在这儿带，触发方就得去翻腿的内部。
        if isinstance(result, dict):
            row["report"] = result
        return row

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
    #
    # 2026-11-17：落池改走 :func:`plobi.collectors.base.land_occurrences`——**全仓唯一一处**
    # 写池的代码。这一层从前自己内联了一份 `PoolItem(...)`，与 `base.pool_items_of`
    # 是两份映射且已经漂移（那份带会话两列、这份不带）。
    from plobi.collectors.base import land_occurrences

    result = land_occurrences(occurrences, source=source)
    return _row(source, ok=True, state="ready", detail=f"拉了 {len(occurrences)} 行，进池 {result['created']} 条新",
                counts=result)
