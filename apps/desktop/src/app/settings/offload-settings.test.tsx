/**
 * 设置 → 外包 那一屏：**只搬字节、说真话**。
 *
 * 三条要钉住的：
 * 1. 后端给的那一句原因不许被前端改写，得原样落进悬停；
 * 2. 右边只有两态：没配好是加号（点开是引导），配好了才是开关；
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

  return {
    ...actual,
    getOffloadAgents: (refresh?: boolean) => getOffloadAgents(refresh)
  }
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
      enabled: true,
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
      enabled: true,
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
      enabled: false,
      why_not: '本机没有 agy：不是坏了，是没装。',
      resume_supported: true,
      today: { rounds: 0, total_usd: null, cost_status: 'unknown', last_ts: '' },
      last_round: null
    },
    {
      name: 'kiro',
      installed: true,
      binary: 'C:/Users/Turtlelet/AppData/Local/Kiro-Cli/kiro-cli.exe',
      version: '2.35.1',
      logged_in: { value: null, source: 'unknown', detail: '还没派过活' },
      login_hint: false,
      permission_flag: '--trust-all-tools',
      permission_modes: [],
      capabilities: { writes: true },
      can_use: true,
      enabled: true,
      why_not: '',
      resume_supported: false,
      today: { rounds: 0, total_usd: null, cost_status: 'unknown', last_ts: '' },
      last_round: null
    },
    {
      name: 'qwen',
      installed: true,
      binary: 'D:/Runtimes/Languages/Node/qwen.cmd',
      version: '0.26.0',
      logged_in: { value: null, source: 'unknown', detail: '还没派过活' },
      login_hint: false,
      permission_flag: '--approval-mode yolo',
      permission_modes: [],
      capabilities: { writes: true },
      can_use: true,
      enabled: true,
      why_not: '',
      resume_supported: false,
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
  it('renders one row per vendor; the backend’s reason is kept verbatim in the hover tip', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Qoder')).toBeTruthy())

    expect(screen.getByText('CodeBuddy')).toBeTruthy()
    expect(screen.getAllByText('Antigravity').length).toBeGreaterThan(0)
    const tips = [...document.querySelectorAll('[title]')].map(node => node.getAttribute('title') || '')
    expect(tips.some(tip => tip.includes(payload.agents[0].why_not))).toBe(true)
    expect(tips.some(tip => tip.includes(payload.agents[2].why_not))).toBe(true)
    expect(getOffloadAgents).toHaveBeenCalledWith(false)
  })

  // 用户 10-30：「agy 能不能用别人的官方首字母大写名称，不要用命令行简写」。
  it('屏幕上写品牌名，不写内部 id', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Antigravity')).toBeTruthy())

    // 新接的两家也照这个口径（11-08）。
    expect(screen.getByText('Kiro')).toBeTruthy()
    expect(screen.getByText('Qwen Code')).toBeTruthy()

    // 内部 id 只留在 tooltip 与台账里，不上屏。
    expect(screen.queryByText('qwen')).toBeNull()
    expect(screen.queryByText('kiro')).toBeNull()
  })

  it('没配好的右边是加号，点开是登录引导', async () => {
    getOffloadAgents.mockResolvedValue(payload)

    render(<OffloadSettings />)

    await waitFor(() => expect(screen.getByText('Qoder')).toBeTruthy())

    // 没配好（qoder 未登录、agy 未安装）右边是加号，不是开关
    const setupButtons = screen.getAllByLabelText(/Not set up yet/)

    expect(setupButtons.length).toBe(2)

    fireEvent.click(setupButtons[0])

    await waitFor(() => expect(screen.getByText(/sign in inside its own app/)).toBeTruthy())
  })

  it('reads again when the refresh control is used', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(getOffloadAgents).toHaveBeenCalled())

    fireEvent.click(screen.getByText('Read again'))
    await waitFor(() => expect(getOffloadAgents).toHaveBeenCalledWith(true))
  })
})
