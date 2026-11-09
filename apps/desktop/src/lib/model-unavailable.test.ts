import { describe, expect, it } from 'vitest'

import { remainingPhrase, unavailableOf } from './model-unavailable'

const WORDS = { minutes: '{n} 分钟后', hours: '{n} 小时后', days: '{n} 天后' }

describe('unavailableOf', () => {
  it('reads the hub’s "waiting" state off a model row', () => {
    const state = unavailableOf({
      unavailable: { reason: 'quota_exhausted', retry_after_seconds: 1754, available_at: '2026-11-08T23:13:02' }
    })

    expect(state).toEqual({
      reason: 'quota_exhausted',
      retry_after_seconds: 1754,
      available_at: '2026-11-08T23:13:02'
    })
  })

  it('a model with no verdict is pickable', () => {
    expect(unavailableOf({ context: { max: 200000 } })).toBeNull()
    expect(unavailableOf(undefined)).toBeNull()
    expect(unavailableOf(null)).toBeNull()
  })

  it('a verdict without a reason is not a verdict', () => {
    // The hub always writes a reason; a row that carries only a timestamp says
    // nothing about why, so it must not grey the row out.
    expect(unavailableOf({ unavailable: { retry_after_seconds: 60 } })).toBeNull()
    expect(unavailableOf({ unavailable: {} })).toBeNull()
  })

  it('a missing or non-numeric wait is "unknown", not a wrong number', () => {
    const state = unavailableOf({ unavailable: { reason: 'quota_exhausted' } })

    expect(state?.retry_after_seconds).toBeNull()
  })
})

describe('remainingPhrase', () => {
  it('says the time left in the app’s own words', () => {
    expect(remainingPhrase(1754, WORDS)).toBe('30 分钟后')
    expect(remainingPhrase(3600, WORDS)).toBe('1 小时后')
    expect(remainingPhrase(7200, WORDS)).toBe('2 小时后')
    expect(remainingPhrase(86400, WORDS)).toBe('1 天后')
  })

  it('rounds up, so a bucket with 61 s left does not read as "0 分钟"', () => {
    expect(remainingPhrase(61, WORDS)).toBe('2 分钟后')
    expect(remainingPhrase(1, WORDS)).toBe('1 分钟后')
  })

  it('no time left means no phrase at all', () => {
    expect(remainingPhrase(0, WORDS)).toBeNull()
    expect(remainingPhrase(-5, WORDS)).toBeNull()
    expect(remainingPhrase(null, WORDS)).toBeNull()
    expect(remainingPhrase(undefined, WORDS)).toBeNull()
    expect(remainingPhrase(Number.NaN, WORDS)).toBeNull()
  })
})
