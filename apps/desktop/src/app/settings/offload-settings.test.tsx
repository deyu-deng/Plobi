/**
 * 设置 → 外包 那一屏：**只搬字节、说真话**。
 *
 * 三条要钉住的：
 * 1. 后端给的那一句原因不许被前端改写，得原样落进悬停；
 * 2. 右边只有两态：没配好是加号（点开是引导），配好了才是开关；
 * 3. 回跳能力有就写有、没有就写没有（这一屏不放假按钮，所以只断言文字）。
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { OffloadSettings } from './offload-settings'
import type * as OffloadApi from './offload/api'
import type { OffloadPayload } from './offload/api'

// Radix 的下拉要 pointer-capture / scrollIntoView / ResizeObserver，jsdom 一个都没有；
// 补上这几样，下拉才开得了（与 approval.test.tsx 同一套办法）。
beforeAll(() => {
  const proto = window.HTMLElement.prototype as unknown as Record<string, () => unknown>

  const stubs: Record<string, () => unknown> = {
    hasPointerCapture: () => false,
    releasePointerCapture: () => undefined,
    scrollIntoView: () => undefined,
    setPointerCapture: () => undefined
  }

  for (const [name, fn] of Object.entries(stubs)) {
    proto[name] ??= fn
  }

  if (!('ResizeObserver' in window)) {
    ;(window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

const getOffloadAgents = vi.fn()
const startOffloadLogin = vi.fn()
const setOffloadAgentAccount = vi.fn()

vi.mock('./offload/api', async () => {
  const actual = await vi.importActual<typeof OffloadApi>('./offload/api')

  return {
    ...actual,
    getOffloadAgents: (refresh?: boolean) => getOffloadAgents(refresh),
    startOffloadLogin: (name: string) => startOffloadLogin(name),
    setOffloadAgentAccount: (name: string, account: string) => setOffloadAgentAccount(name, account)
  }
})

const payload: OffloadPayload = {
  agents: [
    {
      name: 'qoder',
      accounts: [],
      selected_account: '',
      dispatch_ready: true,
      install: { command: '', package: '', homepage: '', binary: 'qodercli', note: '这台机器上是 node 全局包 @qoder-ai/qodercli；registry 上查不到这一家', verified: false },
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
      // 多账号（11-16）：这一族下面有两个身份；厂商当前登录着的是第一个。
      accounts: [
        { id: '2fface12-fe18-4517-a6f7-48009aa628c8', label: '190****0997', current: true },
        { id: '8fabe04c-3f5f-4851-b9d6-43d3b63256d2', label: '8fabe04c', current: false }
      ],
      selected_account: '',
      dispatch_ready: true,
      install: { command: 'npm i -g @tencent-ai/codebuddy-code', package: '@tencent-ai/codebuddy-code', homepage: 'https://cnb.cool/codebuddy/codebuddy-code', binary: 'codebuddy', note: '', verified: true },
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
      accounts: [],
      selected_account: '',
      dispatch_ready: true,
      install: null,
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
      accounts: [],
      selected_account: '',
      dispatch_ready: true,
      install: { command: '', package: '', homepage: 'https://kiro.dev/docs', binary: 'kiro-cli', note: '官方安装器（不在 npm）', verified: true },
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
      accounts: [],
      selected_account: '',
      dispatch_ready: true,
      install: { command: 'npm i -g @qwen-code/qwen-code', package: '@qwen-code/qwen-code', homepage: 'https://github.com/QwenLM/qwen-code', binary: 'qwen', note: '', verified: true },
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
    },
    {
      name: 'amp',
      accounts: [],
      selected_account: '',
      installed: false,
      binary: '',
      version: '',
      logged_in: { value: null, source: 'unknown', detail: '这一家还没接派活腿' },
      login_hint: false,
      permission_flag: '',
      permission_modes: [],
      capabilities: {},
      can_use: false,
      enabled: true,
      why_not: '这一家还没接派活腿',
      resume_supported: false,
      dispatch_ready: false,
      install: {
        command: 'npm i -g @sourcegraph/amp',
        package: '@sourcegraph/amp',
        homepage: 'https://ampcode.com/docs',
        binary: 'amp',
        note: '这一家还没接派活腿：装好之后这里只给安装与登录引导',
        verified: true
      },
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
  vi.useRealTimers()
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

    // 没配好（qoder 未登录、agy 未安装）右边是加号，不是开关。
    // 11-14 起还有第三种「没配好」：**探不到登录态**（kiro / qwen 的 logged_in 是 null）——
    // 探不到 ≠ 没登录，但它同样不配开关，所以也落在加号这一侧（不是亮着的假开关）。
    // 11-15 再加一种：**还没接派活腿**的那几家（amp）——同样只给「＋」，不给点不动的开关。
    const setupButtons = screen.getAllByLabelText(/Not set up yet/)

    expect(setupButtons.length).toBe(5)

    fireEvent.click(setupButtons[0])

    await waitFor(() => expect(screen.getByText(/sign in inside its own app/)).toBeTruthy())
  })

  // 11-14 现读修：三态里「探不到」与「没登录」**不是同一句话**。
  it('探不到登录态与没登录，两句话不一样', async () => {
    getOffloadAgents.mockResolvedValue(payload)

    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Qoder')).toBeTruthy())

    // qoder：后端明说上次没登录 ⇒ 悬停里是那句「上次连接时显示未登录」。
    const qoderTip = screen.getByText('Qoder').getAttribute('title') || ''
    expect(qoderTip).toContain('The last connection reported not signed in')

    // kiro：探不到（value=null）⇒ 悬停里是「暂无登录状态记录」那一句，**不是**未登录那句。
    const kiroTip = screen.getByText('Kiro').getAttribute('title') || ''
    expect(kiroTip).toContain('No sign-in status available yet')
    expect(kiroTip).not.toContain('not signed in')

    // 点开 kiro 的加号，给的也是「暂无登录状态记录」，不冒充「没登录」。
    fireEvent.click(screen.getAllByLabelText(/Not set up yet/)[2])
    await waitFor(() => expect(screen.getByText(/No sign-in status available yet/)).toBeTruthy())
  })

  it('reads again when the refresh control is used', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(getOffloadAgents).toHaveBeenCalled())

    fireEvent.click(screen.getByText('Read again'))
    await waitFor(() => expect(getOffloadAgents).toHaveBeenCalledWith(true))
  })

  // 用户 11-08 19:4x：「点击对应卡片旁边的 + 号，看看会不会自动拉起登录流程」。
  it('点「+」会拉起这一家的登录；拉不起来也不假装', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    startOffloadLogin.mockResolvedValue({ started: false, reason: '这一家没有能一键拉起的登录流程' })

    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Qoder')).toBeTruthy())

    fireEvent.click(screen.getAllByLabelText(/Not set up yet/)[0])

    await waitFor(() => expect(startOffloadLogin).toHaveBeenCalledWith('qoder'))
    // 拉不起来时**屏上不新增句子**：仍是那一句引导。
    expect(screen.getByText(/sign in inside its own app/)).toBeTruthy()
  })

  it('拉起成功后过几秒重探一次登录态（授权那一下要主人自己做）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    getOffloadAgents.mockResolvedValue(payload)
    startOffloadLogin.mockResolvedValue({ started: true, command: 'copilot login --web-flow' })

    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Qoder')).toBeTruthy())

    fireEvent.click(screen.getAllByLabelText(/Not set up yet/)[0])
    await waitFor(() => expect(startOffloadLogin).toHaveBeenCalledWith('qoder'))

    vi.advanceTimersByTime(4000)
    await waitFor(() => expect(getOffloadAgents).toHaveBeenCalledWith(true))
  })

  // 用户 11-08 19:5x：「这个卡片和 Provider 里面的卡片没有共用同一个样式」。
  // 诊断：卡壳本来就是同一件（`SettingsGroup` → `.dl-card`），差的是**行**——
  // Providers 那些行第一格是一枚品牌方块（`EntityAvatar` 那一族），外包这一屏
  // 第一格却是一颗状态点，行下还挂着几行正文（没装／自己关着／今天几轮）。
  // 落地按卡片语法 §1 与 11-07 统一令：第一格给品牌方块，状态由控件的形状说话，
  // 机器细节与用量收进悬停。
  it('每行第一格是品牌方块，跟 Providers 那一屏同一件', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Qoder')).toBeTruthy())

    const avatars = [...document.querySelectorAll('[data-testid^="offload-vendor-avatar-"]')]

    expect(avatars.length).toBe(payload.agents.length)
    // 方块壳归 `EntityAvatar`（32px 的方块：品牌字形或首字母）。
    expect(avatars[0].querySelector('span[aria-hidden="true"]')).toBeTruthy()
  })

  it('状态由控件的形状说话：卡面上不写状态句，机器细节与用量进悬停', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Qoder')).toBeTruthy())

    // 「没装」「自己关掉的」「今天几轮」都不再占卡面（行上不写状态句子）。
    expect(screen.queryByText('not installed on this machine')).toBeNull()
    expect(screen.queryByText(/This switch is off/)).toBeNull()
    expect(screen.queryByText(/today 1/)).toBeNull()

    // 但它们一句都没丢：都在悬停里（后端给的原因仍是原话，不许改写）。
    const tips = [...document.querySelectorAll('[title]')].map(node => node.getAttribute('title') || '')

    expect(tips.some(tip => tip.includes('today 1 · — · 2026-10-22 15:11'))).toBe(true)
    expect(tips.some(tip => tip.includes(payload.agents[0].why_not))).toBe(true)
    expect(tips.some(tip => tip.includes('not installed on this machine'))).toBe(true)
  })
  // 用户 11-15 13:0x：「假设用户的是全新的电脑。只下了我们的 Plobi。用户第一次点配置的『+』的时候。
  // 没有下载就引导用户下载相关软件并引导登录。」
  it('未装的那家点「+」给的是**它自己**的安装命令与下载页，不是那句泛泛的话', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Amp')).toBeTruthy())

    fireEvent.click(screen.getByLabelText(/Not set up yet.*amp/))

    await waitFor(() => expect(screen.getByText('npm i -g @sourcegraph/amp')).toBeTruthy())
    expect(screen.getByText('Open download page')).toBeTruthy()
    // 泛泛那一句**不出现**（有它自己的事实就不给泛泛的话）。
    expect(screen.queryByText(/Not found on this machine/)).toBeNull()
  })

  it('还没接派活腿的那家：给安装引导、不给开关、也不去拉起登录命令', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('Amp')).toBeTruthy())

    // 那一格是「＋」，不是开关（给一颗点不动的开关比不给更坏）。
    expect(screen.getByLabelText(/Not set up yet.*amp/)).toBeTruthy()
    expect(screen.queryByLabelText(/Allow handing work to it · amp/)).toBeNull()

    fireEvent.click(screen.getByLabelText(/Not set up yet.*amp/))

    await waitFor(() => expect(screen.getByText('This one is not wired for dispatch yet.')).toBeTruthy())
    // 还没接派活腿 ⇒ 没有能拉起的登录命令 ⇒ 不去拉起（不假装）。
    expect(startOffloadLogin).not.toHaveBeenCalledWith('amp')
  })

  // 用户 11-16：「一家一行，账号做下拉」＋「我希望可以有多账号体系」。
  it('一家一行，账号做下拉：只有这一族有账号时，那一行才多一颗下拉', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('CodeBuddy')).toBeTruthy())

    // 全屏**只有这一颗**账号下拉（其余几家没做多账号 ⇒ 一个字都不多）。
    const triggers = screen.getAllByLabelText(/^Account · /)
    expect(triggers.length).toBe(1)

    // 它显示的是这一族**当前登录着**的那个身份（没替主人挑别的）。
    expect(triggers[0].textContent).toContain('190****0997')
  })

  it('选了哪一个身份就记哪一个', async () => {
    getOffloadAgents.mockResolvedValue(payload)
    setOffloadAgentAccount.mockResolvedValue(undefined)
    render(<OffloadSettings />)
    await waitFor(() => expect(screen.getByText('CodeBuddy')).toBeTruthy())

    const trigger = screen.getAllByLabelText(/^Account · /)[0]

    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: 'mouse' })

    // 选项在 portal 里（`role="option"`）。选中走 Radix 的**键盘那条路**
    // （`SELECTION_KEYS`＝Enter／空格）⇒ 这也正是键盘用户选它的方式。
    const option = await waitFor(() => {
      const found = [...document.querySelectorAll('[role="option"]')].find(node =>
        (node.textContent || '').includes('8fabe04c')
      )

      expect(found).toBeTruthy()

      return found as HTMLElement
    })

    fireEvent.keyDown(option, { key: 'Enter' })

    await waitFor(() =>
      expect(setOffloadAgentAccount).toHaveBeenCalledWith('codebuddy', '8fabe04c-3f5f-4851-b9d6-43d3b63256d2')
    )
  })
})
