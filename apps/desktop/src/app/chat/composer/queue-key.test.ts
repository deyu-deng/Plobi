import { describe, expect, it } from 'vitest'

import { pendingQueueKey } from '@/store/composer-queue'

import { composerQueueKey, pendingQueueScope } from './queue-key'

// The rule this file pins: **the queue key always exists**.
//
// A conversation that has no session id yet (a brand-new chat, whose first
// message is still on its way to the gateway) used to key the queue with
// `null`, and the queue store dropped the message on the floor — the user
// typed, pressed Enter, and nothing happened (user 11-13:
// 「输入后。发送不了了」). The key now falls back to a per-Agent placeholder
// bucket.
describe('composerQueueKey', () => {
  it('uses the stored session id when the conversation has one', () => {
    expect(
      composerQueueKey({
        pathname: '/agent/agenda-secretary',
        queueSessionKey: 'stored-1',
        sessionId: 'runtime-1'
      })
    ).toBe('stored-1')
  })

  it('falls back to the runtime session id', () => {
    expect(composerQueueKey({ pathname: '/agent/a', queueSessionKey: null, sessionId: 'runtime-1' })).toBe('runtime-1')
  })

  it('never returns nothing for a brand-new chat (the message used to vanish)', () => {
    const key = composerQueueKey({
      pathname: '/agent/agenda-secretary',
      queueSessionKey: null,
      sessionId: null
    })

    expect(key).toBe(pendingQueueKey('agenda-secretary'))
    expect(key.trim()).not.toBe('')
  })

  it('gives two Agents two buckets (a pending message is never misdelivered)', () => {
    const a = composerQueueKey({ pathname: '/agent/a', queueSessionKey: null, sessionId: null })
    const b = composerQueueKey({ pathname: '/agent/b', queueSessionKey: null, sessionId: null })

    expect(a).not.toBe(b)
  })

  it('keeps the L1 chief secretary in its own bucket', () => {
    expect(composerQueueKey({ pathname: '/', queueSessionKey: null, sessionId: null })).toBe(pendingQueueKey('l1'))
  })

  it('keeps the plain chat route out of the L1 bucket', () => {
    expect(composerQueueKey({ pathname: '/new', queueSessionKey: null, sessionId: null })).toBe(pendingQueueKey('chat'))
  })
})

// The placeholder bucket is per Agent: two Agents' new chats must never share
// one, or a pending message would surface in the wrong conversation.
describe('pendingQueueScope', () => {
  it('buckets L1, each L2 secretary and the plain chat route apart', () => {
    expect(pendingQueueScope('/')).toBe('l1')
    expect(pendingQueueScope('/console')).toBe('l1')
    expect(pendingQueueScope('/agent/agenda')).toBe('agenda')
    expect(pendingQueueScope('/new')).toBe('chat')
  })

  it('gives two Agents two different buckets', () => {
    expect(pendingQueueScope('/agent/a')).not.toBe(pendingQueueScope('/agent/b'))
  })
})
