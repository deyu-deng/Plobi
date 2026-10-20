import { describe, expect, it } from 'vitest'

import type { Agent } from '@/app/console/types'

import { recipientInitialValue, recipientOptions, recipientToPayload } from './recipient-options'

/**
 * 这一格守三条：选项只从名册来；「不指定」＝那个键不给；已有作业里那个名字查不到时
 * 不许被静默改成"不指定"（那会把一个真在跑的归属说成没有）。
 */

function row(over: Partial<Agent> & { id: string }): Agent {
  return { name: over.id, status: 'idle', todayCalls: 0, ...over } as Agent
}

describe('recipientOptions', () => {
  it('keeps the roster order and the roster names — no re-sorting, no profiles', () => {
    const options = recipientOptions([row({ id: 'aura', name: 'Aura' }), row({ id: 'kit', name: 'Kit' })])

    expect(options).toEqual([{ id: 'aura', name: 'Aura' }, { id: 'kit', name: 'Kit' }])
  })

  it('drops empty ids and duplicates instead of showing two rows for one agent', () => {
    const options = recipientOptions([
      row({ id: 'aura' }),
      row({ id: '' }),
      row({ id: 'aura', name: 'Aura 重名登记' }),
      undefined as unknown as Agent
    ])

    expect(options.map(option => option.id)).toEqual(['aura'])
  })

  it('falls back to the id when a row carries no display name', () => {
    expect(recipientOptions([row({ id: 'nymo', name: '' })])).toEqual([{ id: 'nymo', name: 'nymo' }])
  })
})

describe('recipientToPayload', () => {
  it('sends nothing for 不指定 instead of a fake agent name', () => {
    for (const empty of ['', '   ', undefined as unknown as string]) {
      expect(recipientToPayload(empty)).toBeUndefined()
    }
  })

  it('passes a chosen agent through trimmed', () => {
    expect(recipientToPayload('  aura ')).toBe('aura')
  })
})

describe('recipientInitialValue', () => {
  it('keeps a name the roster no longer has, rather than silently unsetting it', () => {
    expect(recipientInitialValue('resonote')).toBe('resonote')
  })

  it('treats missing / blank as 不指定', () => {
    for (const blank of [null, undefined, '  ']) {
      expect(recipientInitialValue(blank)).toBe('')
    }
  })
})
