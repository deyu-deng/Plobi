// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpertRoleRow, useAgentExperts } from './expert-panel'
import { EXPERT_ROLES, latestRun, runsOfRole } from './expert-roles'
import type { ExpertRun } from './expert-runs'

/**
 * 11-01 起专家不再是「一整格面板 + 点开弹窗」，也不再是「一行一条会话流水」：
 * 「专家」抽屉里放的是**四类角色的名册**（文件 / 浏览器 / 软件 / 电脑），点一行就打开
 * 这个角色最近那条会话（用户选定的形态：「一个专家一张嘴」）。
 *
 * 所以这里测三件事：取数钩子、角色行本身、以及「角色 ↔ 会话」的挑法。
 */

const api = vi.fn()

const DATA = {
  ok: true,
  data: {
    experts: [
      { goal: '把三份资料折成一份汇总', id: 'kid-1', messages: 4, model: 'glm', parent_session_id: 'p1', role: 'file', started_at: Date.now() / 1000 - 30, status: 'running', tool_calls: 2 },
      { goal: '查一下这门课的作业', id: 'kid-2', messages: 6, model: 'glm', parent_session_id: 'p1', role: 'browser', started_at: Date.now() / 1000 - 600, status: 'done', tool_calls: 5 }
    ],
    live: 1
  }
}

beforeEach(() => {
  api.mockReset()
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { ...(window.plobiDesktop || {}), api }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** 钩子本身没有 DOM，挂一个最小宿主把它的返回值摆到屏上。 */
function Roster({ agentId }: { agentId: string }) {
  const experts = useAgentExperts(agentId)

  return (
    <ul>
      {experts.map(expert => (
        <li key={expert.id}>{expert.id}</li>
      ))}
    </ul>
  )
}

describe('useAgentExperts', () => {
  it('读 /api/agents/:id/experts，名单原样交出来', async () => {
    api.mockResolvedValue(DATA)
    render(<Roster agentId="Sindri" />)

    expect(await screen.findByText('kid-1')).toBeTruthy()
    expect(screen.getByText('kid-2')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/agents/Sindri/experts' }))
  })

  it('每 3.5 秒重读一次：L2 刚派出去的那一条会自己冒出来', async () => {
    vi.useFakeTimers()

    try {
      api.mockResolvedValue(DATA)
      render(<Roster agentId="Sindri" />)

      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      const first = api.mock.calls.length

      await act(async () => { await vi.advanceTimersByTimeAsync(3500) })

      expect(api.mock.calls.length).toBeGreaterThan(first)
    } finally {
      vi.useRealTimers()
    }
  })

  it('没派过就是空名单——不抛，也不留一句解释句', async () => {
    api.mockResolvedValue({ ok: true, data: { experts: [], reason: '名册里 Sindri 还没有会话' } })
    const { container } = render(<Roster agentId="Sindri" />)

    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(container.querySelectorAll('li')).toHaveLength(0)
  })

  it('读失败也只交空名单：一次抖动不该把整栏打挂', async () => {
    api.mockRejectedValue(new Error('500'))
    const { container } = render(<Roster agentId="Sindri" />)

    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(container.querySelectorAll('li')).toHaveLength(0)
  })
})

describe('专家角色名册', () => {
  const run = (over: Partial<ExpertRun>): ExpertRun => ({
    goal: '干点活',
    id: 'kid-x',
    messages: 2,
    model: 'glm',
    parent_session_id: 'p1',
    role: 'file',
    started_at: 100,
    status: 'done',
    tool_calls: 0,
    ...over
  })

  it('四类角色，顺序与后端 ORDER 一致（文件 / 浏览器 / 软件 / 电脑）', () => {
    expect(EXPERT_ROLES.map(role => role.key)).toEqual(['file', 'browser', 'software', 'computer'])
  })

  it('按角色挑会话，大小写与空白都不影响', () => {
    const runs = [run({ id: 'a', role: 'file' }), run({ id: 'b', role: 'Browser' }), run({ id: 'c', role: ' file ' })]

    expect(runsOfRole(runs, 'file').map(item => item.id)).toEqual(['a', 'c'])
    expect(runsOfRole(runs, 'browser').map(item => item.id)).toEqual(['b'])
    expect(runsOfRole(runs, 'computer')).toEqual([])
  })

  it('「一张嘴」取最近那条：派过多次也只认最后一次', () => {
    const runs = [run({ id: 'old', started_at: 10 }), run({ id: 'new', started_at: 90 }), run({ id: 'mid', started_at: 50 })]

    expect(latestRun(runs)?.id).toBe('new')
    expect(latestRun([])).toBeNull()
  })
})

describe('ExpertRoleRow', () => {
  const running: ExpertRun = {
    goal: '把三份资料折成一份汇总',
    id: 'kid-1',
    messages: 4,
    model: 'glm',
    parent_session_id: 'p1',
    role: 'file',
    started_at: Date.now() / 1000 - 65,
    status: 'running',
    tool_calls: 2
  }

  const fileRole = EXPERT_ROLES[0]

  it('一行就是图标 + 名词；没派过时灰着不可点', () => {
    render(<ExpertRoleRow onOpen={() => {}} role={fileRole} runs={[]} />)

    const row = screen.getByRole('button', { name: /File/ })

    expect(row.textContent?.trim()).toBe('File')
    expect(row.hasAttribute('disabled')).toBe(true)
  })

  it('派过就能点，正在干的有状态点与时长', () => {
    render(<ExpertRoleRow onOpen={() => {}} role={fileRole} runs={[running]} />)

    const row = screen.getByRole('button', { name: /File/ })

    expect(row.hasAttribute('disabled')).toBe(false)
    expect(screen.getByLabelText('running')).toBeTruthy()
    // 只认「算出了分:秒」这件事：整秒的边界上 1:05 会变成 1:04，写死就会偶发红。
    expect(row.getAttribute('title')).toMatch(/\d:\d{2}/)
  })

  it('同一个角色派过多次才显示次数——一次是常态，不该多一个数', () => {
    const { rerender } = render(<ExpertRoleRow onOpen={() => {}} role={fileRole} runs={[running]} />)

    expect(screen.getByRole('button', { name: /File/ }).textContent?.trim()).toBe('File')

    rerender(<ExpertRoleRow onOpen={() => {}} role={fileRole} runs={[running, { ...running, id: 'kid-9', status: 'done' }]} />)
    expect(screen.getByRole('button', { name: /File/ }).textContent).toContain('2')
  })

  it('点一行 = 打开它那张嘴（用户：「一个专家一张嘴」）', () => {
    const onOpen = vi.fn()
    render(<ExpertRoleRow onOpen={onOpen} role={fileRole} runs={[running]} />)

    fireEvent.click(screen.getByRole('button', { name: /File/ }))

    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
