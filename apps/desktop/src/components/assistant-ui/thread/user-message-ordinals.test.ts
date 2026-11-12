import { describe, expect, it } from 'vitest'

import { userOrdinalsFor } from './user-message'

/**
 * 「最后一条 user 是谁」与「我是第几条 user」——两个只跟转录有关的答案。
 *
 * 这两个答案原来由**每条**用户气泡各扫一遍整段转录算出来（一个从尾往前找、
 * 一个从头数），代价是 O(气泡数 × 转录长度)。现在按转录数组的身份缓存一份，
 * 所有气泡共用 ⇒ 一次转录变更只走一遍。
 */
describe('userOrdinalsFor', () => {
  const transcript = [
    { id: 'u1', role: 'user' },
    { id: 'a1', role: 'assistant' },
    { id: 'u2', role: 'user' },
    { id: 'a2', role: 'assistant' },
    { id: 'u3', role: 'user' }
  ]

  it('数出每条 user 的序号（只数 user）', () => {
    const { ordinalById } = userOrdinalsFor(transcript)

    expect(ordinalById.get('u1')).toBe(0)
    expect(ordinalById.get('u2')).toBe(1)
    expect(ordinalById.get('u3')).toBe(2)
    expect(ordinalById.has('a1')).toBe(false)
  })

  it('指出最后一条 user', () => {
    expect(userOrdinalsFor(transcript).latestUserId).toBe('u3')
  })

  it('没有 user 时不编一个出来', () => {
    const { latestUserId, ordinalById } = userOrdinalsFor([{ id: 'a1', role: 'assistant' }])

    expect(latestUserId).toBeNull()
    expect(ordinalById.size).toBe(0)
  })

  it('同一份转录只算一次（缓存按数组身份命中）', () => {
    expect(userOrdinalsFor(transcript)).toBe(userOrdinalsFor(transcript))
  })

  it('转录换了就重算', () => {
    const next = [...transcript, { id: 'u4', role: 'user' }]

    expect(userOrdinalsFor(next)).not.toBe(userOrdinalsFor(transcript))
    expect(userOrdinalsFor(next).latestUserId).toBe('u4')
    expect(userOrdinalsFor(next).ordinalById.get('u4')).toBe(3)
  })
})
