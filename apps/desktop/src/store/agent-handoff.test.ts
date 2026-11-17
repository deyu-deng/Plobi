import { afterEach, describe, expect, it } from 'vitest'

import { $agentHandoff, clearAgentHandoff, stageAgentHandoff, takeAgentHandoff } from './agent-handoff'

/**
 * 「发给别的智能体」那笔待办（WP-SELECTION-TOOLBAR）。
 *
 * 判据只有一条要紧的：**取出即清**——同一条话不许被两个输入框各发一次；
 * 以及**作用域键对不上就不取**（不会把话发到当前这个智能体嘴里）。
 */
describe('agent handoff', () => {
  afterEach(() => {
    clearAgentHandoff()
  })

  it('作用域键对得上才取走', () => {
    stageAgentHandoff({ agentId: 'aura', targetKey: 'agent:aura', text: '这段话给光环' })

    expect(takeAgentHandoff('agent:other')).toBeNull()
    expect($agentHandoff.get()?.text).toBe('这段话给光环')
  })

  it('取走即清：同一条不会被两个输入框各发一次', () => {
    stageAgentHandoff({ agentId: 'aura', targetKey: 'agent:aura', text: '这段话给光环' })

    const taken = takeAgentHandoff('agent:aura')

    expect(taken?.text).toBe('这段话给光环')
    expect($agentHandoff.get()).toBeNull()
    expect(takeAgentHandoff('agent:aura')).toBeNull()
  })

  it('不记待办时取不到东西', () => {
    expect(takeAgentHandoff('agent:aura')).toBeNull()
  })
})
