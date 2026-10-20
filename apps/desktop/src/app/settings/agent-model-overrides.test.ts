import { describe, expect, it } from 'vitest'

import type { RosterAgent } from './agent-model-overrides'
import {
  canShowFollowDefault,
  explicitModelOf,
  overrideStateOf,
  pickerOverrides,
  splitQualified
} from './agent-model-overrides'

/**
 * 这一档的判据全部围绕一句假话：`model` 是生效值，它说不出「这行到底自己有没有指定」。
 * 装机副本与 10-09 那个打包壳根本不返 `modelOverride` ⇒ 「读不到」必须是第三种状态，
 * 不许被洗成「跟随默认」（那既撒谎，又会让第三档画出一颗必然 400 的按钮）。
 */

function agent(over: Partial<RosterAgent> & { id: string }): RosterAgent {
  return { name: over.id, status: 'idle', todayCalls: 0, ...over } as RosterAgent
}

describe('overrideStateOf', () => {
  it('marks a row with no flag as unknown, not as following the default', () => {
    const row = agent({ id: 'aura', model: 'deepseek/deepseek-chat' })

    expect(overrideStateOf(row)).toBe('unknown')
    expect(explicitModelOf(row)).toBeNull()
  })

  it('separates "following" from "explicitly the same value" — the whole reason this exists', () => {
    const following = agent({ id: 'kit', model: 'deepseek/deepseek-chat', modelOverride: false })
    const explicit = agent({ id: 'aura', model: 'deepseek/deepseek-chat', modelOverride: true })

    expect(following.model).toBe(explicit.model)
    expect(overrideStateOf(following)).toBe('following')
    expect(overrideStateOf(explicit)).toBe('explicit')
    expect(explicitModelOf(explicit)).toBe('deepseek/deepseek-chat')
    expect(explicitModelOf(following)).toBeNull()
  })
})

describe('canShowFollowDefault', () => {
  it('refuses the 跟随默认 option when any row is silent about its own override', () => {
    const honest = agent({ id: 'kit', modelOverride: false })
    const silent = agent({ id: 'prism', model: 'openrouter/x' })

    expect(canShowFollowDefault([honest])).toBe(true)
    expect(canShowFollowDefault([honest, silent])).toBe(false)
  })

  it('treats an empty roster as "nothing said", not as "everything follows"', () => {
    expect(canShowFollowDefault([])).toBe(false)
  })
})

describe('pickerOverrides', () => {
  it('carries only rows that say they set their own model, and keeps following rows absent', () => {
    const table = pickerOverrides([
      agent({ id: 'aura', model: 'deepseek/deepseek-chat', modelOverride: true }),
      agent({ id: 'kit', model: 'deepseek/deepseek-chat', modelOverride: false })
    ])

    expect(table.aura).toBe('deepseek/deepseek-chat')
    expect('kit' in table).toBe(true)
    expect(table.kit).toBeUndefined()
  })
})

describe('splitQualified', () => {
  it('splits at the first slash so model ids may contain slashes', () => {
    expect(splitQualified('anthropic/claude-sonnet/4')).toEqual({
      model: 'claude-sonnet/4',
      provider: 'anthropic'
    })
  })

  it('refuses anything that is not provider/model instead of guessing a provider', () => {
    for (const bad of ['', 'deepseek-chat', '/only-model', 'provider/', '  /x']) {
      expect(splitQualified(bad)).toBeNull()
    }
  })
})
