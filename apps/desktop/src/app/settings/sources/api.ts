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
  /**
   * 这条信源**自己声明**要填哪几格（`SourceSpec.setup_fields`）。
   * `label` 是**英文缺省值**：界面先查语言目录的 `settings.sources.fields.<key>`，
   * 查不到才回它——所以第三方包零改动就能出现在界面上（只是没翻译）。
   */
  setup_fields: SetupField[]
  source: string
  state: string
  touched_after_last_write: boolean

  // —— 10-22 `WP-SOURCE-SURFACE` 新加的这几格：**三态由后端算**，前端不再留第二套词表 ——

  /** 未连接 / 已连接 / 出错。判据只有一处：`sources_api.status_of`（后端）。 */
  status?: 'connected' | 'disconnected' | 'error'
  /** 给界面查语言目录用的原因码（**不是给人看的句子**，裁定 45.4：slug 不上屏）。 */
  status_code?: string
  /** 出错时那一句人话，**逐字**来自上一轮执行方（不转述、不总结）。正常态恒为空。 */
  status_detail?: string
  /** 上一轮真打的时间与状态词（抽屉里那一行「上一次」）。 */
  last_run_at?: string
  last_state?: string
  /** 每源一行频率：生效值 + 它从哪来 + 下一次该打在几点 + 到没到点（后端算的，见 `cadence_env`）。 */
  cadence?: string
  cadence_origin?: 'config' | 'per_round' | 'spec' | null
  cadence_state?: string
  cadence_note?: string
  next_run_at?: string
  due?: boolean
  /** 这一条在**信息池**里几条。旧屏只有日程库那个 `events`，于是按裁 193 只进池的教务
   *  在这屏永远是 0。`-1` 是"池读不到"，不是"零条"。 */
  pool_count?: number
  pool_kinds?: Record<string, number>
  /** 从这条**自己声明的那个网站**抓来的图标（data URL）。空串＝没有，退品牌表再退首字母。 */
  icon?: string
  site?: string
  /**
   * 这条信源**自己叫什么**（用户 10-24：「网站就显示网站名就行了，不要叫什么"教务"」）。
   *
   * 后端那一格是**读数**：声明名优先，没声明而它是「网站」那一类就吃站点自报的 `<title>`。
   * 界面语言目录仍然排在它前面（我们自己那几条四语各有写法，与 `setup_fields.label`
   * 那一格同一套路）——所以第三方包零改动也有名字，而屏上不会再出现「教务（…）」那种
   * 由界面替网站起的类目词。
   */
  display_name?: string
  /** 配置卡第三格那句"这是什么"（信源自己声明；空＝这一行不画）。 */
  summary?: string
  /**
   * 这条在本机**成不成立**（只有声明了探针的那几条才有这一格）。
   *
   * `null`／缺位 = 这条没有前置要说；`ready=false` 时 `code` 拿去查语言目录、`detail` 进抽屉。
   * 它的存在理由是把一颗**假故障**换成真话：钉钉那种"要装一个本机客户端、而这台机器上
   * 它从没登录过"是**未连接**，不是"我们的产品坏了"。
   */
  probe?: null | { code: string, detail: string, ready: boolean }
  /** 密钥那一格**设过没有**。值永不出现在这条读数里（那是"星号"那一格的牙齿）。 */
  secrets?: Record<string, boolean>
  /**
   * 这一条**还缺必填吗**——**后端算的那一格**（`sources_api` 里 `setup_ready` 的反面）。
   *
   * 前端从前自己算一遍（只看 `setup_fields` 里 required 的那几格），于是微信／钉钉／幕布
   * 那三条**一个必填格都没有**的被恒判成「配好了」——加号就变成了开关（用户 11-08 现场：
   * 用户明明什么都没有配置，钉钉、幕布和微信左边就从加号变成开关了）。
   *
   * 缺位是**读数**：后端是旧版、没给这一格 ⇒ 退回本地那套算法（见 `./verbs`）。
   */
  needs_setup?: boolean
  /**
   * 首次配置要用户**人肉做**的那一步（装软件 / 登录）——**由这一条自己声明**。
   * 缺位＝这一条没有这一步（邮箱／教务那一族，填表就是全部）。
   */
  setup_guide?: null | { action: string, detail: string, title: string, url: string }
  /**
   * 这一条**按会话准入**吗（微信那种一条信源里有很多群与人的形状）。
   *
   * 由后端从声明给（`SourceSpec.conversation_scoped`），面不许按信源名字自己决定画不画。
   */
  conversation_scoped?: boolean
}

export interface SetupField {
  help: string
  key: string
  /**
   * `secret`：`type=password` 且**只写不读**（值走 `.env`，读数里只有"设过没有"，留空＝不改）。
   * `cadence`：预设下拉，存进去的仍是那一句 cron（运行时一个字没改，改的只是画法）。
   */
  kind: 'cadence' | 'path' | 'secret' | 'text' | 'url'
  label: string
  required: boolean
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

/**
 * 拆信封。**这一层是整个模块最容易搞错的地方，所以单独一个函数并配一条用例。**
 *
 * 后端这几条路由回的是 `{ ok: true, data: … }`，而 `fetchJson`（electron/main.ts）
 * **只 JSON.parse、不解包**——所以 `window.plobiDesktop.api()` 拿到的是**整个信封**。
 * （`/api/collect/*` 别处回的是裸对象，那几条才是这一族里的异类；改它们是破坏性的，
 * 所以这里适配，不动它们。）
 *
 * **解不出来就抛，并说清期望的形状** —— 不许静默给一个空对象：空对象会让下游拿到
 * `undefined.sources`，然后崩在离原因十万八千里的一行上。第一版就是这么崩的：
 * 「Cannot read properties of undefined (reading 'map')」指向 `.map`，而真正的原因
 * 在这里少解了一层信封 ——**崩的地方永远不是出问题的地方**。
 */
export function unwrapData<T>(envelope: unknown, what: string): T {
  if (envelope && typeof envelope === 'object' && 'data' in (envelope as { data?: unknown })) {
    return (envelope as { data: T }).data
  }

  throw new Error(
    `The backend answered ${what} with a shape this panel does not recognise — expected ` +
    '{ ok: true, data: … } but the top level has no "data". If you just changed the backend, ' +
    'restart it: the backend is a separate process and does not pick up code changes.'
  )
}

/**
 * 信封解出来之后**还必须过一次形状校验**。
 *
 * `unwrapData` 已经挡了「外层少一层信封」这一种（第一版就这么崩过），但它只能
 * 证明顶层有 `data`，证明不了 `data` 里有没有这一屏要用的两个字段。装机现场真崩
 * 过一次：老后端（只有 `liveness_api.py` 那一版）回的 `data` 是活性读数，没有
 * `sources` / `categories`，于是 `groupByCategory()` 里 `categories.map` 抛
 * `Cannot read properties of undefined (reading 'map')`，**整个应用被根 error
 * boundary 吞掉**——崩的地方（`.map`）离出问题的地方（后端契约比面旧）隔了十万八
 * 千里。所以：字段不对就在这儿抛，把话说到骨子上，让设置页走它本来就有的
 * 「读不到」那一格，而不是让用户对着一个崩溃的白屏猜。
 *
 * 真实原因八成是**后端比面前端旧**（装机目录里的 checkout 没更新到带着
 * `sources_api.py` 的那一笔）——所以那句话里带着「重启后端／更新」的提示。
 */
export function parseSourcesPayload(data: unknown, what: string): SourcesPayload {
  const payload = (data ?? {}) as Partial<SourcesPayload> & Record<string, unknown>

  if (!Array.isArray(payload.sources)) {
    throw new Error(
      `The backend answered ${what} with a payload this panel cannot render — expected ` +
      '`sources` to be an array. The running backend looks OLDER than this app: restart the ' +
      'backend (or update the install), then reopen this page.'
    )
  }

  if (!Array.isArray(payload.categories)) {
    throw new Error(
      `The backend answered ${what} with a payload this panel cannot render — expected ` +
      '`categories` to be an array. The running backend looks OLDER than this app: restart the ' +
      'backend (or update the install), then reopen this page.'
    )
  }

  const badRow = payload.sources.find(row => !row || typeof row.source !== 'string')

  if (badRow) {
    throw new Error(
      `The backend answered ${what} with a payload this panel cannot render — a row without a ` +
      'string `source` came back. The running backend looks OLDER than this app: restart the ' +
      'backend (or update the install), then reopen this page.'
    )
  }

  return payload as SourcesPayload
}

export async function getSources(): Promise<SourcesPayload> {
  return parseSourcesPayload(
    unwrapData<unknown>(await window.plobiDesktop.api({ path: '/api/collect/sources' }), '/api/collect/sources'),
    '/api/collect/sources'
  )
}

/**
 * 那颗开关的**另一半**：这一条自己那个本机服务被怎么处理了。
 *
 * 用户 10-23：「我希望开始和关闭是会真的切断这条信源的采集程序」——所以后端不只是写一个布尔，
 * 它按声明（`sidecar_port` / `sidecar_start`）去拉起或收掉那个进程，并把结果回给面。
 * 没声明端口的信源这一格是 `undefined`（面因此什么都不多说一句）。
 *
 * 面**只认 `listening` 这个读数**，句子在自己那四语目录里拼（裁定 45.4：后端给状态，界面给话）。
 */
export interface SidecarNote {
  action: string
  detail: string
  listening: boolean
  port: number
  /**
   * 拉起之前那一步准备（如"先替他取一次本机密钥"）说的那句话。缺位＝这一条没有准备那一步。
   * 面照样不认识它是谁的准备：后端给读数，界面只把这句话并进那一条通知里（裁定 45.4）。
   */
  prepare?: string
}

export async function setSourceEnabled(sourceId: string, enabled: boolean): Promise<SidecarNote | undefined> {
  const data = unwrapData<Record<string, unknown>>(await window.plobiDesktop.api({
    path: `/api/collect/sources/${encodeURIComponent(sourceId)}/enabled`,
    method: 'POST',
    body: { enabled }
  }), `/api/collect/sources/${sourceId}/enabled`)

  const note = data?.sidecar as Partial<SidecarNote> | undefined

  return typeof note?.listening === 'boolean'
    ? { action: note.action ?? '', detail: note.detail ?? '', listening: note.listening, port: Number(note.port ?? 0) }
    : undefined
}

/**
 * 跑这一条一轮真采集。
 *
 * 它**会真的动网络/磁盘**，不是零成本查询——所以调用方必须先把这件事写在界面上
 * （按钮文案里那句「会真的跑一次」），不许让用户以为只是刷新一下。
 */
export async function trySource(sourceId: string): Promise<TryResult> {
  const path = `/api/collect/sources/${encodeURIComponent(sourceId)}/try`

  return unwrapData<TryResult>(await window.plobiDesktop.api({ path, method: 'POST' }), path)
}

export async function saveSourceSetup(sourceId: string, params: Record<string, string>): Promise<void> {
  await window.plobiDesktop.api({
    path: `/api/collect/sources/${encodeURIComponent(sourceId)}/setup`,
    method: 'POST',
    body: { params }
  })
}

/**
 * 把这一条**产品能代做的那一步**做掉（微信那枚数据密钥），然后由面重读读数。
 *
 * 为什么需要它：用户 11-08「如果用户点击配置了就引导用户登录或者下载软件」——用户装好
 * 客户端、登录完之后，得**有一个办法让产品重新看一眼**；微信那一枚密钥是产品自己取的
 * （`SourceSpec.sidecar_prepare`，裁定 203 口径 2「数据密钥不由他抄」）。
 *
 * **不翻开关**：翻开关是另一件事（`setSourceEnabled`）。这一格只做准备。
 */
export async function prepareSource(sourceId: string): Promise<{ detail: string, ok: boolean }> {
  const data = unwrapData<Record<string, unknown>>(
    await window.plobiDesktop.api({
      path: `/api/collect/sources/${encodeURIComponent(sourceId)}/prepare`,
      method: 'POST'
    }),
    `/api/collect/sources/${sourceId}/prepare`
  )

  return { detail: String(data?.detail ?? ''), ok: data?.ok === true }
}

/** 外面装的那些第三方信源包。现读，不是记一份账。 */
export interface ExternalPackage {
  distribution: string
  entry_point: string
  /** 装上了但载不进来时的一格理由；`source` 那时是 null。 */
  load_error: null | string
  source: null | string
}

export async function getExternalPackages(): Promise<ExternalPackage[]> {
  const body = unwrapData<{ packages: ExternalPackage[] }>(
    await window.plobiDesktop.api({ path: '/api/collect/sources/external' }), '/api/collect/sources/external'
  )

  return body.packages ?? []
}

/**
 * 一扇会话（微信的一个群/一个人、钉钉的一个 cid）在界面上的那一行。
 *
 * `status` 是**这条信源自己算好交出来的**（`known` 采 / `excluded` 不采 / `pending` 没定过＝采）——
 * 名单与状态都来自后端那两只声明的手（`SourceSpec.conversations` / `admission`），
 * 界面不认识任何一家信源的名字。
 */
export interface ConversationRow {
  id: string
  /** 名单那一档，三值闭集：`group`／`private`／`official`（判据与词表在
   * `plobi/collectors/chatlog/client.py` 的 `ROSTER_KINDS` 那段，**与池里 `visibility` 两档不是一张表**）。 */
  kind: string
  name: string
  status: string
  /** 这一扇最后一条在什么时候（他 10-26：「加时间，并按它倒序」）。按会话的信源都该给，
   * 第三方包没给就是空串——空串排到最后，不编一个时间出来。 */
  last_at?: string
  /** `name` 是真名字还是走投无路拿了会话号顶上。缺这一格当作**没查到**（宁缺勿假）。 */
  resolved?: boolean
}

export interface ConversationsPayload {
  conversations: ConversationRow[]
  reason: string
  state: 'not_supported' | 'ok' | 'unavailable'
}

/**
 * 这一条信源今天有哪些会话、各自采不采。
 *
 * 三家共用这一条读口（微信 / 钉钉 / 任何第三方按会话的信源）。读不到不是错误：
 * `state` 会把"这条没有会话这一层"（`not_supported`）与"有，但现在拿不到"（`unavailable`）
 * 分开说——**0 行**只在 `state=ok` 时才是"今天确实没有会话"这句真话。
 */
export async function getConversations(sourceId: string): Promise<ConversationsPayload> {
  const path = `/api/collect/sources/${encodeURIComponent(sourceId)}/conversations`
  const body = unwrapData<Partial<ConversationsPayload>>(await window.plobiDesktop.api({ path }), path)

  return {
    conversations: body.conversations ?? [],
    reason: body.reason ?? '',
    state: body.state ?? 'not_supported'
  }
}

/** 改一扇会话的准入档位，回的是**改完之后的整份名单**（界面因此不必再读一次）。 */
export async function setConversationStatus(
  sourceId: string, id: string, status: string
): Promise<ConversationsPayload> {
  const path = `/api/collect/sources/${encodeURIComponent(sourceId)}/conversations`

  const body = unwrapData<Partial<ConversationsPayload>>(await window.plobiDesktop.api({
    method: 'POST', path, body: { id, status }
  }), path)

  return {
    conversations: body.conversations ?? [],
    reason: body.reason ?? '',
    state: body.state ?? 'not_supported'
  }
}

/**
 * 装 / 卸一个第三方包。**这一轮是异步的**：回的是动作名，不是结果。
 * 拿那个名字去轮日志（`getActionStatus`），成败由**退出码**说话。
 *
 * 名字闸门不在这一层——它在 CLI 那一层。这里只 spawn。
 */
export async function installExternalPackage(packageName: string): Promise<string> {
  const body = unwrapData<{ name: string }>(
    await window.plobiDesktop.api({
      path: '/api/collect/sources/external/install',
      method: 'POST',
      body: { package: packageName, upgrade: true }
    }),
    '/api/collect/sources/external/install'
  )

  return body.name
}

export async function removeExternalPackage(packageName: string): Promise<string> {
  const body = unwrapData<{ name: string }>(
    await window.plobiDesktop.api({
      path: '/api/collect/sources/external/remove',
      method: 'POST',
      body: { package: packageName }
    }),
    '/api/collect/sources/external/remove'
  )

  return body.name
}

/**
 * 用户选一个文件进信息池（`WP-SOURCE-FIRST-RUN-IMPORT`）。**不是信源动词**：
 * 没有开关、没有频率，路径来自系统选文件框。回 `{file, format, parsed, created, updated, unchanged}`。
 */
export async function importSourceFile(path: string): Promise<{
  file: string
  format: string
  parsed: number
  created: number
  updated: number
  unchanged: number
}> {
  return unwrapData(
    await window.plobiDesktop.api({
      path: '/api/collect/import',
      method: 'POST',
      body: { path }
    }),
    '/api/collect/import'
  )
}
