// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as PlobiApi from '@/plobi'

import { AgentOffloadPanel } from './agent-offload-panel'
import type { OffloadRoundWithReply } from './agent-offloads'

const getAgentOffloads = vi.fn()
const offloadResume = vi.fn()

vi.mock('@/plobi', async importOriginal => ({
  ...(await importOriginal<typeof PlobiApi>()),
  getAgentOffloads: (agentId: string) => getAgentOffloads(agentId),
  offloadResume: (agent: string, sessionId: string) => offloadResume(agent, sessionId),
}))

function round(over: Partial<OffloadRoundWithReply> = {}): OffloadRoundWithReply {
  return {
    agent: 'codebuddy',
    artifacts: [{ path: 'D:/projects/Aura/today.py', sha256_12: '3da942743391' }],
    brief: '把今天的读数写成页',
    cost_status: 'usage_reported_without_price',
    cwd: 'D:/projects/Aura',
    duration_seconds: 16.4,
    permission_flag: '-y --permission-mode=bypassPermissions',
    ts: '2026-10-13T01:00:00+00:00',
    ...over,
  }
}

function payload(rounds: OffloadRoundWithReply[], unattributedReason = '') {
  return {
    data: {
      agent: 'l2-aura',
      bound_folder: 'D:/projects/Aura',
      cost_status: 'usage_reported_without_price',
      rounds,
      total_rounds: rounds.length,
      total_usd: null,
      unattributed_reason: unattributedReason,
    },
    ok: true,
  }
}

const openExternal = vi.fn()

beforeEach(() => {
  openExternal.mockReset()
  openExternal.mockResolvedValue(undefined)
  offloadResume.mockReset()
  offloadResume.mockResolvedValue({ data: { agent: 'codebuddy', argv: [], cwd: 'D:/projects/Aura', pid: 4242 }, ok: true })
  // The renderer only ever has this bridge in the desktop shell.
  // `api` 是「外部会话」那一组要用的；这一档只关心外包行，给它一个空表。
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: {
      ...(window.plobiDesktop || {}),
      api: vi.fn().mockResolvedValue({ data: { projects: [], reason: '', sessions: [], vendor: 'qoder' }, ok: true }),
      openExternal
    }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderPanel(rounds: OffloadRoundWithReply[], unattributedReason = '') {
  getAgentOffloads.mockResolvedValue(payload(rounds, unattributedReason))
  const view = render(<AgentOffloadPanel agentId="l2-aura" />)

  // 台账没读完时整格是 null（连「专家」抽屉都没有），等它上桌再断言——
  // 之前等的是加载句，而加载句早就不画了，于是等了个空。
  await screen.findByText('Experts')

  return view
}

describe('AgentOffloadPanel', () => {
  it('按外包对象分组：一家软件一行（图标 + 名字 + 折角）', async () => {
    await renderPanel([round(), round({ ts: '2026-10-12T00:00:00+00:00' }), round({ agent: 'agy' })])

    // 11-01：软件行不再重复会话数——那个数在一级抽屉头上已经有了。
    // （`SiCodebuddy` 自带一个 <title>CodeBuddy</title>，所以名字会出现两遍，别按整串比。）
    const codebuddy = screen.getByRole('button', { name: /CodeBuddy/ })

    expect(codebuddy.textContent).toContain('CodeBuddy')
    expect(codebuddy.textContent).not.toMatch(/\d/)
    // 10-30 裁定：屏上写品牌名（Antigravity），内部 id（agy）不上屏。
    expect(screen.getByRole('button', { name: /Antigravity/ })).toBeTruthy()
  })

  it('分组行是品牌方块（与 Providers 那一屏同一件），不再是那枚中性「机器人」', async () => {
    await renderPanel([round({ agent: 'agy' })])

    // 11-09：厂商 id → 腿 id 的对照表搬进共用的品牌名那张表，这儿画的是 Providers 那颗方块。
    expect(screen.getByTestId('vendor-brand-block-agy')).toBeTruthy()
    // 那枚「机器人」不再替任何一家认领身份。
    expect(screen.queryByTestId('offload-vendor-fallback-agy')).toBeNull()
  })

  it('点组头才展开，展开后这一段活、权限档与花费都在', async () => {
    await renderPanel([round()])

    expect(screen.queryByText('把今天的读数写成页')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    const row = await screen.findByRole('button', { name: /把今天的读数写成页/ })
    const text = row.textContent || ''

    expect(text).toContain('bypassPermissions')
    expect(text).toContain('16s')
    expect(text).toContain('cost unknown')
    expect(text).toContain('usage_reported_without_price')
    expect(text).not.toContain('$0')
  })

  it('开着这一格时它会自己重读：L2 刚外包完，不用重新点进来', async () => {
    vi.useFakeTimers()

    try {
      getAgentOffloads.mockResolvedValue(payload([round()]))
      render(<AgentOffloadPanel agentId="l2-aura" />)

      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(getAgentOffloads).toHaveBeenCalledTimes(1)

      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(getAgentOffloads).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('按积分记账的一行显示积分，不显示"花费未知"也不显示 $0', async () => {
    await renderPanel([round({ cost_status: 'credits_only', usage: { total_cost_usd: 0, total_credits: 3.775 } })])
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    const row = await screen.findByRole('button', { name: /把今天的读数写成页/ })

    expect(row.textContent).toContain('3.78 credits')
    expect(row.textContent).not.toContain('cost unknown')
    expect(row.textContent).not.toContain('$0')
  })

  it('它写回来的回执原文就在行里，不用再跳去外部软件去猜', async () => {
    await renderPanel([round({ reply: '# Sindri — 工作目录', reply_status: 'filed' })])
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    const row = await screen.findByRole('button', { name: /把今天的读数写成页/ })

    expect(row.textContent).toContain('It said: # Sindri — 工作目录')
  })

  it('点一行是把落盘的产物交给主进程打开（file:// 而不是裸路径）', async () => {
    await renderPanel([round()])
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    fireEvent.click(await screen.findByRole('button', { name: /把今天的读数写成页/ }))

    await waitFor(() => expect(openExternal).toHaveBeenCalledTimes(1))
    expect(openExternal).toHaveBeenCalledWith('file:///D:/projects/Aura/today.py')
  })

  it('没产物的一行不可点，也不会假装去开', async () => {
    await renderPanel([round({ artifacts: [], failure: '没登录，起不来' })])
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    const row = await screen.findByRole('button', { name: /把今天的读数写成页/ })

    expect(row.hasAttribute('disabled')).toBe(true)
    expect(row.textContent).toContain('没登录，起不来')

    fireEvent.click(row)
    expect(openExternal).not.toHaveBeenCalled()
  })

  it('有会话号的一行：点它是交给后端那个白名单口，不碰 openExternal', async () => {
    await renderPanel([round({ external_session: '01a1248d-3b15-738e', external_session_source: 'result.session_id' })])
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    const row = await screen.findByRole('button', { name: /把今天的读数写成页/ })

    // 读数先上桌：会话号与"在那台软件里打开这条会话"都在
    expect(row.textContent).toContain('01a1248d-3b15-738e')
    expect(row.hasAttribute('disabled')).toBe(false)

    fireEvent.click(row)

    await waitFor(() => expect(offloadResume).toHaveBeenCalledTimes(1))
    expect(offloadResume.mock.calls[0]).toEqual(['codebuddy', '01a1248d-3b15-738e'])
    expect(openExternal).not.toHaveBeenCalled()
  })

  it('后端拒了这一跳，就把后端那句话说出来，不甩 JSON 给用户', async () => {
    offloadResume.mockRejectedValue(new Error('400: {"ok":false,"error":"这一家还没有实测过的回跳旗标"}'))
    await renderPanel([round({ external_session: '01a1248d-3b15' })])
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    fireEvent.click(await screen.findByRole('button', { name: /把今天的读数写成页/ }))

    const row = await screen.findByText(/这一家还没有实测过的回跳旗标/)

    expect(row.textContent).not.toContain('{"ok"')
  })

  it('空表不再留整句：后端给了归属原因也不在栏里铺一行解释', async () => {
    await renderPanel([], '这一条没有绑定目录')

    // 10-28 起这一格空着就是空着（屏上不放解释句）；11-01 起空表连表头也不留——
    // 中段那枚切换已经说了这是「外包」这一页。
    expect(screen.queryByText('这一条没有绑定目录')).toBeNull()
    expect(screen.queryByText('Outsourced')).toBeNull()
  })

  it('主进程打不开就当场写明打不开，不留"点了没反应"', async () => {
    openExternal.mockRejectedValue(new Error('Invalid external URL'))
    await renderPanel([round()])
    fireEvent.click(screen.getByRole('button', { name: /CodeBuddy/ }))

    fireEvent.click(await screen.findByRole('button', { name: /把今天的读数写成页/ }))

    expect(await screen.findByText(/could not open/i)).toBeTruthy()
  })

  it('库里在、但没留下可读对话的那家：照实给一行人话（后端给的那句原话）', async () => {
    const api = vi.fn().mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve({ ok: true, data: { projects: [], reason: '', sessions: [], sources: [
          {
            readable: false,
            reason: 'Cursor 这台机器上没留下可读的对话',
            root: 'D:/Data/AppData/Cursor',
            sendable: false,
            sessions: 0,
            vendor: 'cursor'
          }
        ] } })
      }

      return Promise.resolve({ ok: true, data: {} })
    })

    Object.defineProperty(window, 'plobiDesktop', {
      configurable: true,
      value: { ...(window.plobiDesktop || {}), api }
    })

    await renderPanel([])

    // 照实说，不改写：不说「它没有对话」（不属实），也不假装读得到。
    expect(await screen.findByText('Cursor 这台机器上没留下可读的对话')).toBeTruthy()
    expect(screen.getByTestId('external-session-unreadable-cursor')).toBeTruthy()
  })
})
