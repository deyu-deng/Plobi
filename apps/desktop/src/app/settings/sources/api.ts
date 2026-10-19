/**
 * 信源区的取数层（设置 → 信源）。
 *
 * 后端那一条读入口是 `GET /api/collect/sources`（`plobi/collectors/sources_api.py`，
 * 挂在既有 `/api/collect` 前缀下）。**读只有这一个入口** —— 面上不该有两个地方能读到
 * "某条信源现在什么状态"，两个入口就是两份可能不一致的读数。
 *
 * 这一层只搬字节。**不翻译、不排序、不算状态**：状态与时刻是读数，句子由语言目录给
 * （`Docs/specs/source-layer.md` §6.1），动词由 `./verbs` 那张表给。搬运的中途改一次
 * 读数，就是把"后端说了算"悄悄换成"前端说了算"。
 */

export interface SourceRow {
  age_days: null | number
  /** 注册表那个 `collecting` 开关点没点（`null` = 这一条不在注册表里，如 `manual`）。 */
  collecting: null | boolean
  /** 生效值：用户在设置里翻过的优先于代码里的默认。`null` = **读不到**，不是"没点"。 */
  enabled: null | boolean
  /** `enabled` 是从 `sources.yaml` 来的还是从 `SourceSpec` 来的——只给布尔而不说
   *  从哪来，「我明明关了怎么还在跑」这句话就永远答不上。 */
  enabled_origin: 'config' | 'spec' | null
  category: null | string
  events: number
  /** 注册表说在采、但没有任何一方构造得出执行方 ⇒ 产品缺陷，不是设置。 */
  has_builder: null | boolean
  last_created_at: string
  note: string
  requires_manual_setup: null | boolean
  /** 首次配置产出的**非密钥**参数（路径 / 地址 / 账号名）。 */
  setup: null | Record<string, unknown>
  source: string
  state: string
  touched_after_last_write: boolean
}

export interface SourceCategoryGroup {
  id: string
  sources: string[]
}

export interface SourcesPayload {
  categories: SourceCategoryGroup[]
  checked_at: string
  sources: SourceRow[]
  stale_after_days: number
  verdict: string
}

/**
 * 「试一次」的回执。`state` / `detail` **逐字**来自执行方
 * （`sweep.py` 或采集器自己的状态词表）——这一层不转述、不总结、不代写（P6）。
 */
export interface TryResult {
  detail: string
  ok: boolean
  source: string
  state: string
}

export async function getSources(): Promise<SourcesPayload> {
  return window.plobiDesktop.api<SourcesPayload>({ path: '/api/collect/sources' })
}

export async function setSourceEnabled(sourceId: string, enabled: boolean): Promise<void> {
  await window.plobiDesktop.api<{ enabled: boolean }>({
    path: `/api/collect/sources/${encodeURIComponent(sourceId)}/enabled`,
    method: 'POST',
    body: { enabled }
  })
}

/**
 * 跑这一条一轮真采集。
 *
 * 它**会真的动网络/磁盘**，不是零成本查询——所以调用方必须先把这件事写在界面上
 * （按钮文案里那句「会真的跑一次」），不许让用户以为只是刷新一下。
 */
export async function trySource(sourceId: string): Promise<TryResult> {
  return window.plobiDesktop.api<TryResult>({
    path: `/api/collect/sources/${encodeURIComponent(sourceId)}/try`,
    method: 'POST'
  })
}

export async function saveSourceSetup(sourceId: string, params: Record<string, string>): Promise<void> {
  await window.plobiDesktop.api<{ setup: Record<string, string> }>({
    path: `/api/collect/sources/${encodeURIComponent(sourceId)}/setup`,
    method: 'POST',
    body: { params }
  })
}