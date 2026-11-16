/**
 * 设置 → 外包 的取数层（**只搬字节**）。
 *
 * 后端那一条读入口是 `GET /api/offload/agents`（`plobi/delegation/offload_surface.py`）。
 * 它与 Providers 那一屏不是一件事：那边借订阅/API 当模型，这边是"把活交给那家软件去做"。
 * 状态与判据全在后端（`availability()` 一份），这里**不算状态、不排序、不翻译**。
 */

export interface OffloadLogin {
  /** `true` 登录着 / `false` 它自己说没登录 / `null` 没有证据（**不许读成不能**）。 */
  value: boolean | null
  /** `last-run`（台账里上一趟的真证据）/ `acp`（厂商自己在 ACP 那一腿给的答复）/ `local`（本地痕迹）/ `unknown`。 */
  source: string
  detail: string
}

export interface OffloadLastRound {
  ts: string
  cwd: string
  duration_seconds: number | null
  reply_status: string
  answer_excerpt: string
  external_session: string
}

export interface OffloadInstall {
  /** 装它的那条命令（人话，可复制到终端跑）。空 = 没有可复制的命令，只给官方页。 */
  command: string
  package: string
  /** 官方下载页（点开就下载或看下载说明）。 */
  homepage: string
  binary: string
  /** 一句说明（有什么坑）。机器细节，进悬停。 */
  note: string
  /** 这条安装事实是不是现核过的（false ⇒ 照官方文档写的，没在本机实测过）。 */
  verified: boolean
}

export interface OffloadAccount {
  /** 账号标识（厂商那边那一串 id）。 */
  id: string
  /** 显示名（厂商把昵称加密了就读不出来 ⇒ 退到标识前八位，不给空白）。 */
  label: string
  /** 厂商那边当前登录着的是这个（这一族里第一份身份文件说了算）。 */
  current: boolean
}

export interface OffloadAgent {
  name: string
  installed: boolean
  binary: string
  version: string
  logged_in: OffloadLogin
  /** 存在性猜的那个（凭据痕迹非空）；**只是提示，不是判据**（10-22 它绿着骗过一回）。 */
  login_hint: boolean
  permission_flag: string
  permission_modes: string[]
  capabilities: Record<string, boolean>
  can_use: boolean
  why_not: string
  /** 主人是否允许把活交给它（设置里那格开关）。默认允许；关了只影响派活，不改可用性判据。 */
  enabled: boolean
  resume_supported: boolean
  /**
   * 这一家**能不能派活**（真源表那一行说了算）。
   *
   * false = 屏上看得见、能拿到下载引导，但**还没接派活腿** ⇒ 装好了也还是「＋」，
   * 不给一颗点不动的开关（用户 11-15 13:0x：全新电脑上第一次点「+」时给下载引导）。
   */
  dispatch_ready: boolean
  /** 这一家**怎么装**（新用户的第一扇门）。null = 表里没有这一族事实 ⇒ 不编命令。 */
  install: OffloadInstall | null
  /** 这一族下面有哪几个身份（多账号体系，11-16）。空 = 这一族没做多账号。 */
  accounts: OffloadAccount[]
  /** 主人为这一族选的那个身份（空 = 厂商用它自己当前登录的那个）。 */
  selected_account: string
  today: { rounds: number; total_usd: number | null; cost_status: string; last_ts: string }
  last_round: OffloadLastRound | null
}

export interface OffloadPayload {
  agents: OffloadAgent[]
  probed_at: string
  probe_ttl_seconds: number
  /** 后台那一轮 ACP 握手还有几家没答（11-14 起：登录态问厂商自己）。>0 ⇒ 屏上隔几秒自己再读一次。 */
  acp_probe_pending?: number
  note: string
}

export async function getOffloadAgents(refresh = false): Promise<OffloadPayload> {
  const response: unknown = await window.plobiDesktop.api({
    path: `/api/offload/agents${refresh ? '?refresh=1' : ''}`
  })

  const record = response as { data?: unknown } | null
  const payload = (record && typeof record === 'object' && 'data' in record ? record.data : response) as OffloadPayload

  if (!payload || !Array.isArray(payload.agents)) {
    throw new Error('后端回的形状不是外包清单：agents 不是数组')
  }

  return payload
}

export interface SessionSource {
  readable: boolean
  reason: string
  root: string
  sendable: boolean
  sessions: number
  vendor: string
}

/** 本机上各家 AI 软件的会话存在哪、读不读得动（设置 → 外包 那一段）。 */
export async function getSessionSources(): Promise<SessionSource[]> {
  const response: unknown = await window.plobiDesktop.api({ path: '/api/external-sessions/sources' })
  const record = response as { data?: unknown } | null

  const payload = (record && typeof record === 'object' && 'data' in record ? record.data : response) as
    | { sources?: unknown }
    | null

  const sources = payload?.sources

  return Array.isArray(sources) ? (sources as SessionSource[]) : []
}


/** 主人这一格的开关：允许/不允许把活交给这家软件。
 *  落点 config.yaml 的 `delegation.offload_disabled_agents`（空 = 全部允许，向后兼容）。 */
export async function setOffloadAgentEnabled(name: string, enabled: boolean): Promise<void> {
  await window.plobiDesktop.api({
    path: `/api/offload/agents/${encodeURIComponent(name)}/enabled`,
    method: 'PUT',
    body: { enabled }
  })
}

/** 这一族派活用哪一个身份（多账号体系，11-16）。
 *  落点 config.yaml 的 `delegation.offload_accounts`（`{厂商: 账号标识}`）。 */
export async function setOffloadAgentAccount(name: string, account: string): Promise<void> {
  await window.plobiDesktop.api({
    path: `/api/offload/agents/${encodeURIComponent(name)}/account`,
    method: 'PUT',
    body: { account }
  })
}

export interface OffloadLoginLaunch {
  /** `true` = 官方那条登录命令已经拉起来了；**授权那一下仍然要主人自己做**。 */
  started: boolean
  /** 拉不起来时的**一句人话**（这一家没有一键流程／本机没装它）。 */
  reason?: string
  /** 拉起来的那条命令（人话形态，给日志与 tooltip 看）。 */
  command?: string
}

/** 点「+」时**拉起这一家官方那条登录命令**（用户 11-08 19:4x：「看看会不会自动拉起登录流程」）。
 *
 * 拉不起来的照实回一句人话（`started: false`）——**屏上不假装拉起了**。
 */
export async function startOffloadLogin(name: string): Promise<OffloadLoginLaunch> {
  const response: unknown = await window.plobiDesktop.api({
    path: `/api/offload/agents/${encodeURIComponent(name)}/login`,
    method: 'POST'
  })

  const record = (response && typeof response === 'object' && 'data' in response ? response.data : response) as
    | Partial<OffloadLoginLaunch>
    | null

  if (!record || typeof record.started !== 'boolean') {
    throw new Error('后端回的形状不是登录结果')
  }

  return { started: record.started, reason: record.reason, command: record.command }
}
