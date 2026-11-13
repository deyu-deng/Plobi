import { describe, expect, it } from 'vitest'

import { agentScope, chatScopeKey, L1_SCOPE } from '@/app/console/chat/scope'

import { agentScopeKey } from './queue-key'

// The rule this file pins: **the queue bucket IS the Agent**.
//
// The product has no session concept any more — one Agent, one mouth (裁定 37／
// R-015，`north-star-operating-model.md:89`). So the queue is keyed by the
// Agent's scope key, which always exists and never changes. Before that it was
// keyed by a session id: a brand-new chat had no session id yet, the key was
// empty, and the queue store dropped the message on the floor (user 11-13:
// 「输入后。发送不了了」).
describe('agentScopeKey', () => {
  it('gives the L1 chief secretary its own bucket', () => {
    expect(agentScopeKey('/')).toBe('l1')
    expect(agentScopeKey('/console')).toBe('l1')
  })

  it('gives every L2 secretary its own bucket', () => {
    expect(agentScopeKey('/agent/agenda-secretary')).toBe('agent:agenda-secretary')
    expect(agentScopeKey('/agent/butler')).toBe('agent:butler')
  })

  it('never gives two Agents the same bucket (a pending message is never misdelivered)', () => {
    expect(agentScopeKey('/agent/a')).not.toBe(agentScopeKey('/agent/b'))
    expect(agentScopeKey('/agent/a')).not.toBe(agentScopeKey('/'))
  })

  it('keeps the retired plain-chat route out of the L1 bucket', () => {
    expect(agentScopeKey('/new')).toBe('chat')
  })

  it('is the very same key the console uses for that conversation', () => {
    // Two places writing the key shape would drift; the queue and the
    // conversation must be the same identity, byte for byte.
    expect(agentScopeKey('/')).toBe(chatScopeKey(L1_SCOPE))
    expect(agentScopeKey('/agent/agenda-secretary')).toBe(chatScopeKey(agentScope('agenda-secretary')))
  })

  it('always returns a key — there is no "no bucket" state left', () => {
    for (const pathname of ['/', '/console', '/agent/x', '/new', '/settings', '/whatever']) {
      expect(agentScopeKey(pathname).trim()).not.toBe('')
    }
  })
})
