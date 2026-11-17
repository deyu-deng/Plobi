import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { beginEntering, endEntering } from '@/app/chat/agent-enter'
import { $agentEnter } from '@/app/chat/agent-enter-store'
import { $agentHandoff, clearAgentHandoff, stageAgentHandoff } from '@/store/agent-handoff'

import { useAgentHandoff } from './use-agent-handoff'

// 作用域那一档会把真网关客户端拖进来——这一档只关心「什么时候才送」，不关心后端。
vi.mock('@/store/scope', async () => {
  const { atom } = await import('nanostores')

  return {
    $scopeAgent: atom<string>('default'),
    ensureGatewayProfile: vi.fn(async () => undefined),
    normalizeProfileKey: (name: null | string | undefined) => (name ?? '').trim() || 'default'
  }
})

const { $scopeAgent } = await import('@/store/scope')

/**
 * 交付「发给别的智能体」那笔待办（WP-SELECTION-TOOLBAR）。
 *
 * 三个条件全对上才送：作用域键对上了 ／ 不是「正在进入」中 ／ 活动后端就是目标那个。
 * 少一个都不许送——否则这句话会落进**上一个**智能体的嘴里。
 */
describe('useAgentHandoff', () => {
  beforeEach(() => {
    $scopeAgent.set('aura')
    $agentEnter.set(endEntering())
  })

  afterEach(() => {
    // 不卸载的话，上一档那个还挂着的 hook 会把这一档的待办取走（它也订阅着同一个 atom）。
    cleanup()
    clearAgentHandoff()
    $agentEnter.set(endEntering())
  })

  it('条件齐了就送出去，并照平常那条路走', () => {
    const submit = vi.fn()

    stageAgentHandoff({ agentId: 'aura', targetKey: 'agent:aura', text: '这段话给光环' })

    renderHook(() => useAgentHandoff({ scopeKey: 'agent:aura', submit }))

    expect(submit).toHaveBeenCalledWith('这段话给光环')
  })

  it('作用域键对不上时不取——不会把话发到当前这个智能体', () => {
    const submit = vi.fn()

    stageAgentHandoff({ agentId: 'aura', targetKey: 'agent:aura', text: '这段话给光环' })

    renderHook(() => useAgentHandoff({ scopeKey: 'agent:other', submit }))

    expect(submit).not.toHaveBeenCalled()
    expect($agentHandoff.get()?.text).toBe('这段话给光环')
  })

  it('「正在进入」中不送——那一趟（换后端 → 拉会话 → 恢复）还没走完', () => {
    const submit = vi.fn()

    $agentEnter.set(beginEntering('光环'))
    stageAgentHandoff({ agentId: 'aura', targetKey: 'agent:aura', text: '这段话给光环' })

    renderHook(() => useAgentHandoff({ scopeKey: 'agent:aura', submit }))

    expect(submit).not.toHaveBeenCalled()
  })

  it('活动后端不是目标那个时不送（进入失败时控制台会把后端退回去）', () => {
    const submit = vi.fn()

    $scopeAgent.set('default')
    stageAgentHandoff({ agentId: 'aura', targetKey: 'agent:aura', text: '这段话给光环' })

    renderHook(() => useAgentHandoff({ scopeKey: 'agent:aura', submit }))

    expect(submit).not.toHaveBeenCalled()
  })

  it('同一笔待办只送一次', () => {
    const submit = vi.fn()

    stageAgentHandoff({ agentId: 'aura', targetKey: 'agent:aura', text: '这段话给光环' })

    const { rerender } = renderHook(() => useAgentHandoff({ scopeKey: 'agent:aura', submit }))

    act(() => {
      rerender()
    })

    expect(submit).toHaveBeenCalledTimes(1)
  })
})
