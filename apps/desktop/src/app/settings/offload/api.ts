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
  /** `last-run`（台账里上一趟的真证据）/ `unknown` / `probe`（以后显式检查才有）。 */
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
  resume_supported: boolean
  today: { rounds: number; total_usd: number | null; cost_status: string; last_ts: string }
  last_round: OffloadLastRound | null
}

export interface OffloadPayload {
  agents: OffloadAgent[]
  probed_at: string
  probe_ttl_seconds: number
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
