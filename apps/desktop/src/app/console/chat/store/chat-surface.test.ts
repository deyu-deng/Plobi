import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

import { AGENDA_PROPOSE_TOOL, proposePart } from './chat-surface'

function pendingEvent(id: string): AgendaEvent {
  return {
    confirm_seq: 3,
    created_at: '2026-08-30T00:00:00Z',
    id,
    kind: 'class',
    source: 'manual',
    start_at: '2026-08-31T10:00:00',
    status: 'pending',
    title: 't',
    updated_at: '2026-08-30T00:00:00Z'
  }
}

describe('agenda propose part (spec §3.6 shared pending-event card)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('proposePart carries the raw AgendaEvent as args', () => {
    const part = proposePart(pendingEvent('e1'))

    expect(part).toMatchObject({ toolName: AGENDA_PROPOSE_TOOL, type: 'tool-call' })

    const toolPart = part.type === 'tool-call' ? part : null

    expect((toolPart?.args as { id: string }).id).toBe('e1')
  })

  it('uses a stable tool-name constant', () => {
    expect(AGENDA_PROPOSE_TOOL).toBe('agenda_propose')
  })
})
