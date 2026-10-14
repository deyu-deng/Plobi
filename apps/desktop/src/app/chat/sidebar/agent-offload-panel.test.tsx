// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as PlobiApi from '@/plobi'
import type { OffloadRound } from '@/types/plobi'

import { AgentOffloadPanel } from './agent-offload-panel'

const getAgentOffloads = vi.fn()
const offloadResume = vi.fn()

vi.mock('@/plobi', async importOriginal => ({
  ...(await importOriginal<typeof PlobiApi>()),
  getAgentOffloads: (agentId: string) => getAgentOffloads(agentId),
  offloadResume: (agent: string, sessionId: string) => offloadResume(agent, sessionId),
}))

function round(over: Partial<OffloadRound> = {}): OffloadRound {
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

function payload(rounds: OffloadRound[], unattributedReason = '') {
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

function countIn(button: HTMLElement): string {
  return (button.textContent || '').replace(/[^0-9]/g, '')
}

beforeEach(() => {
  openExternal.mockReset()
  openExternal.mockResolvedValue(undefined)
  offloadResume.mockReset()
  offloadResume.mockResolvedValue({ data: { agent: 'codebuddy', argv: [], cwd: 'D:/projects/Aura', pid: 4242 }, ok: true })
  // The renderer only ever has this bridge in the desktop shell.
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { ...(window.plobiDesktop || {}), openExternal },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderPanel(rounds: OffloadRound[], unattributedReason = '') {
  getAgentOffloads.mockResolvedValue(payload(rounds, unattributedReason))
  const view = render(<AgentOffloadPanel agentId="l2-aura" />)

  await waitFor(() => expect(screen.queryByText(/Reading the offload ledger/)).toBeNull())

  return view
}

describe('AgentOffloadPanel', () => {
  it('按外包对象分组：组头给出名字与轮次数', async () => {
    await renderPanel([round(), round({ ts: '2026-10-12T00:00:00+00:00' }), round({ agent: 'agy' })])

    const codebuddy = screen.getByRole('button', { name: /codebuddy/ })

    expect(within_count(codebuddy)).toBe('2')
    expect(within_count(screen.getByRole('button', { name: /agy/ }))).toBe('1')
  })

  it('点组头才展开，展开后这一段活、权限档与花费都在', async () => {
    await renderPanel([round()])

    expect(screen.queryByText('把今天的读数写成页')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /codebuddy/ }))

    const row = await screen.findByRole('button', { name: /把今天的读数写成页/ })
    const text = row.textContent || ''

    expect(text).toContain('bypassPermissions')
    expect(text).toContain('16s')
    expect(text).toContain('cost unknown')
    expect(text).toContain('usage_reported_without_price')
    expect(text).not.toContain('$0')
  })

  it('点一行是把落盘的产物交给主进程打开（file:// 而不是裸路径）', async () => {
    await renderPanel([round()])
    fireEvent.click(screen.getByRole('button', { name: /codebuddy/ }))

    fireEvent.click(await screen.findByRole('button', { name: /把今天的读数写成页/ }))

    await waitFor(() => expect(openExternal).toHaveBeenCalledTimes(1))
    expect(openExternal).toHaveBeenCalledWith('file:///D:/projects/Aura/today.py')
  })

  it('没产物的一行不可点，也不会假装去开', async () => {
    await renderPanel([round({ artifacts: [], failure: '没登录，起不来' })])
    fireEvent.click(screen.getByRole('button', { name: /codebuddy/ }))

    const row = await screen.findByRole('button', { name: /把今天的读数写成页/ })

    expect(row.hasAttribute('disabled')).toBe(true)
    expect(row.textContent).toContain('没登录，起不来')

    fireEvent.click(row)
    expect(openExternal).not.toHaveBeenCalled()
  })

  it('有会话号的一行：点它是交给后端那个白名单口，不碰 openExternal', async () => {
    await renderPanel([round({ external_session: '01a1248d-3b15-738e', external_session_source: 'result.session_id' })])
    fireEvent.click(screen.getByRole('button', { name: /codebuddy/ }))

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
    fireEvent.click(screen.getByRole('button', { name: /codebuddy/ }))

    fireEvent.click(await screen.findByRole('button', { name: /把今天的读数写成页/ }))

    const row = await screen.findByText(/这一家还没有实测过的回跳旗标/)

    expect(row.textContent).not.toContain('{"ok"')
  })

  it('后端给了归属原因时，空表说那句原因而不是"还没外包过"', async () => {
    await renderPanel([], '这一条没有绑定目录')

    expect(await screen.findByText('这一条没有绑定目录')).toBeTruthy()
  })

  it('主进程打不开就当场写明打不开，不留"点了没反应"', async () => {
    openExternal.mockRejectedValue(new Error('Invalid external URL'))
    await renderPanel([round()])
    fireEvent.click(screen.getByRole('button', { name: /codebuddy/ }))

    fireEvent.click(await screen.findByRole('button', { name: /把今天的读数写成页/ }))

    expect(await screen.findByText(/could not open/i)).toBeTruthy()
  })
})

function within_count(button: HTMLElement): string {
  return (button.textContent || '').replace(/[^0-9]/g, '')
}
