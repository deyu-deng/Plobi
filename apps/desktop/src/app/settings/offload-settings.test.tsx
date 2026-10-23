/**
 * 设置 → 外包 那一屏：**只搬字节、说真话**。
 *
 * 三条要钉住的：
 * 1. 不可用的那一家，行上要带后端给的那一句原因（不是前端自己编一句）；
 * 2. 登录态写清来源：上一趟它自己说的「没登录」要出现，**没有证据时不许写成没登录**；
 * 3. 回跳能力有就写有、没有就写没有（这一屏不放假按钮，所以只断言文字）。
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { OffloadSettings } from './offload-settings'
import type * as OffloadApi from './offload/api'
import type { OffloadPayload } from './offload/api'

const getOffloadAgents = vi.fn()

vi.mock('./offload/api', async () => {
  const actual = await vi.importActual<typeof OffloadApi>('./offload/api')

  return { ...actual, getOffloadAgents: (refresh?: boolean) => getOffloadAgents(refresh) }
})

const payload: OffloadPayload = {
  agents: [
    {
      name: 'qoder',
      installed: true,
      binary: 'D:/Runtimes/Languages/Node/qodercli.cmd',
      version: '1.1.75',
      logged_in: { value: false, source: 'last-run', detail: '上一趟它自己说没登录' },
      login_hint: true,
      permission_flag: '--permission-mode default',
      permission_modes: ['default', 'accept_edits'],
      capabilities: { writes: true },
      can_use: false,
      why_not: '它没登录：这一轮不会交给它（登录要本人做一次，不代跑）。',
      resume_supported: false,
      today: { rounds: 1, total_usd: null, cost_status: 'no_usage_reported', last_ts: '2026-10-22T15:11:48+00:00' },
      last_round: {
        ts: '2026-10-22T15:11:48+00:00',
        cwd: 'D:/Projects/Sindri',
        duration_seconds: 9.36,
        reply_status: 'not_filed',
        answer_excerpt: 'Not logged in · Please run /login',
        external_session: '6d6695cc'
      }
    },
    {
      name: 'codebuddy',
      installed: true,
      binary: 'C:/bin/codebuddy.exe',
      version: '2.161.4',
      logged_in: { value: true, source: 'last-run', detail: '' },
      login_hint: true,
      permission_flag: '-y',
      permission_modes: ['default'],
      capabilities: { writes: true },
      can_use: true,
      why_not: '',
      resume_supported: true,
      today: { rounds: 0, total_usd: null, cost_status: 'priced', last_ts: '' },
      last_round: null
    },
    {
      name: 'agy',
      installed: false,
      binary: '',
      version: '',
      logged_in: { value: null, source: 'unknown', detail: '还没派过活' },
      login_hint: false,
      permission_flag: '',
      permission_modes: [],
      capabilities: {},
      can_use: false,
      why_not: '本机没有 agy：不是坏了，是没装。',
      resume_supported: true,
      today: { rounds: 0, total_usd: null, cost_status: 'unknown', last_ts: '' },
      last_round: null
    }
  ],
  probed_at: '2026-10-23T00:30:00+00:00',
  probe_ttl_seconds: 120,
  note: '这一屏管的是把活外包给那家软件；把它的订阅/API 当模型用在 Providers。'
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('settings → offload', () => {
  it('renders one row per vendor and keeps the backend’s reason verbatim', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('qoder')).toBeTruthy())

    expect(screen.getByText('codebuddy')).toBeTruthy()
    expect(screen.getByText('agy')).toBeTruthy()
    expect(screen.getByText(payload.agents[0].why_not)).toBeTruthy()
    expect(screen.getByText(payload.agents[2].why_not)).toBeTruthy()
    expect(getOffloadAgents).toHaveBeenCalledWith(false)
  })

  it('shows what it actually said, and never reports "no evidence" as a logout', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('qoder')).toBeTruthy())

    expect(screen.getByText(/Not logged in · Please run \/login/)).toBeTruthy()
    // 有证据的那一家才出现"上一趟它自己说没登录"；agy 没有证据 ⇒ 只能出现"no evidence yet"
    expect(screen.getAllByText(/on the last run it said it is not logged in/).length).toBe(1)
    expect(screen.getByText(/no evidence yet/)).toBeTruthy()
    // 回跳能力：只有 codebuddy 与 agy 登记了口，qoder 没有
    expect(screen.getAllByText(/no registered way back into its screen yet/).length).toBe(1)
  })

  it('reads again when the refresh control is used', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(getOffloadAgents).toHaveBeenCalled())

    fireEvent.click(screen.getByText('Read again'))
    await waitFor(() => expect(getOffloadAgents).toHaveBeenCalledWith(true))
  })
})
