import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAgents } from '../api'
import type { Agent } from '../types'
import { MOCK_L2_MODEL } from '../types'

import {
  $consoleAgents,
  $consoleAgentsError,
  $consoleAgentsLoading,
  $consoleBlockedAgents,
  $consoleBlockedCount,
  refreshConsoleAgents,
  setConsoleAgents
} from './agents'

vi.mock('../api', () => ({
  fetchAgendaEvents: vi.fn(),
  fetchAgents: vi.fn()
}))

const fetchAgentsMock = vi.mocked(fetchAgents)

function agent(id: string, status: Agent['status']): Agent {
  return { id, model: MOCK_L2_MODEL, name: id, status, todayCalls: 1 }
}

describe('console agents store', () => {
  beforeEach(() => {
    // 自动重试用的是 setTimeout：装假表，既让下面的 advanceTimers 能推进，
    // 也保证上一个用例留下的待触发计时器不会串进来。
    vi.useFakeTimers()
    vi.clearAllMocks()
    $consoleAgents.set([])
    $consoleAgentsError.set(null)
    $consoleAgentsLoading.set(true)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('counts only the states a human must unblock', () => {
    setConsoleAgents([
      agent('idle', 'idle'),
      agent('working', 'working'),
      agent('waiting', 'awaiting_approval'),
      agent('broken', 'error')
    ])

    expect($consoleBlockedAgents.get().map(a => a.id)).toEqual(['waiting', 'broken'])
    expect($consoleBlockedCount.get()).toBe(2)
  })

  it('is not blocked when every agent is healthy', () => {
    setConsoleAgents([agent('idle', 'idle'), agent('working', 'working')])

    expect($consoleBlockedCount.get()).toBe(0)
  })

  it('publishes agents and clears the error on success', async () => {
    fetchAgentsMock.mockResolvedValue([agent('agenda-secretary', 'working')])

    await refreshConsoleAgents()

    expect($consoleAgents.get()).toHaveLength(1)
    expect($consoleAgentsError.get()).toBeNull()
    expect($consoleAgentsLoading.get()).toBe(false)
  })

  it('records the failure and stops loading when the backend is unreachable', async () => {
    fetchAgentsMock.mockRejectedValue(new Error('offline'))

    await refreshConsoleAgents()

    expect($consoleAgentsError.get()).toBe('offline')
    expect($consoleAgentsLoading.get()).toBe(false)
    // A failed refresh must not blank a rail that already had rows.
    expect($consoleAgents.get()).toEqual([])
  })

  it('keeps surviving rows when a later refresh fails', async () => {
    setConsoleAgents([agent('agenda-secretary', 'working')])
    fetchAgentsMock.mockRejectedValue(new Error('offline'))

    await refreshConsoleAgents()

    expect($consoleAgents.get()).toHaveLength(1)
    expect($consoleAgentsError.get()).toBe('offline')
  })

  // 左栏只在挂载时拉一次名册。后端会在「改一个 Agent 的工作目录」那一瞬间重启/换端口，
  // 那一次拉失败过就再没人补——用户看到的是一栏空白直到重开应用（2026-10-26 现场）。
  it('re-reads the roster on its own after a failed read, then stops', async () => {
    fetchAgentsMock.mockResolvedValue([])
    await refreshConsoleAgents() // 一次成功 = 把重试预算还满
    fetchAgentsMock.mockRejectedValue(new Error('offline'))

    await refreshConsoleAgents()
    expect(fetchAgentsMock).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(2_000)
    expect(fetchAgentsMock).toHaveBeenCalledTimes(3)

    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchAgentsMock).toHaveBeenCalledTimes(4)

    // 预算用完：后端真死了不许把请求打成热循环。
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchAgentsMock).toHaveBeenCalledTimes(4)
    expect($consoleAgentsError.get()).toBe('offline')
  })

  it('a successful retry clears the error and ends the retries', async () => {
    fetchAgentsMock.mockResolvedValue([])
    await refreshConsoleAgents()
    fetchAgentsMock.mockRejectedValue(new Error('offline'))

    await refreshConsoleAgents()
    const afterFailure = fetchAgentsMock.mock.calls.length
    expect($consoleAgentsError.get()).toBe('offline')

    fetchAgentsMock.mockResolvedValue([agent('agenda-secretary', 'working')])
    await vi.advanceTimersByTimeAsync(2_000)

    expect($consoleAgentsError.get()).toBeNull()
    expect($consoleAgents.get()).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchAgentsMock.mock.calls.length).toBe(afterFailure + 1) // 成功之后不再试
  })
})
