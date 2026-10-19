"""`/api/collect/sources` 的**旧门牌**（2026-10-19 起指向新家）。

那一格路由 10-15 起住在这里。19 号设置里要做动词（`Docs/specs/source-layer.md`
§6.1），路由从一条读扩成一读三写，于是搬去了 :mod:`plobi.collectors.sources_api`。
本模块留着是因为**有外部引用按这个路径 import**——不是留一份旧副本（纪律 3.4），
是留一个指路牌。

新家比这里多三样：分类 / 生效开关 / 执行方入口，外加三个动词端点。
`/sources` 的**读的那一半逐字没变**（同一个 `read_source_liveness`，同一份
`expected_sources` 取注册表），所以既有消费方读到的形状不受影响。
"""

from plobi.collectors.sources_api import router

__all__ = ["router"]