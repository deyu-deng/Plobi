import { beforeEach, describe, expect, it } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

import {
  $agendaEvents,
  $agendaPending,
  $agendaPendingCount,
  $agendaSelected,
  $agendaSelectedId,
  removeAgendaEvent,
  setAgendaEvents,
  setAgendaSelectedId,
  upsertAgendaEvent
} from './agenda'

function event(overrides: Partial<AgendaEvent> & Pick<AgendaEvent, 'id' | 'start_at'>): AgendaEvent {
  return {
    created_at: '2026-08-24T00:00:00',
    kind: 'task',
    source: 'manual',
    status: 'confirmed',
    title: overrides.id,
    updated_at: '2026-08-24T00:00:00',
    ...overrides
  }
}

describe('agenda store', () => {
  beforeEach(() => {
    $agendaEvents.set([])
    $agendaSelectedId.set(null)
  })

  it('keeps events sorted by start time', () => {
    setAgendaEvents([
      event({ id: 'late', start_at: '2026-08-25T18:00:00' }),
      event({ id: 'early', start_at: '2026-08-25T08:00:00' })
    ])

    expect($agendaEvents.get().map(e => e.id)).toEqual(['early', 'late'])
  })

  it('upserts in place and re-sorts', () => {
    setAgendaEvents([event({ id: 'a', start_at: '2026-08-25T08:00:00' })])
    upsertAgendaEvent(event({ id: 'b', start_at: '2026-08-25T07:00:00' }))

    expect($agendaEvents.get().map(e => e.id)).toEqual(['b', 'a'])

    upsertAgendaEvent(event({ id: 'b', start_at: '2026-08-25T09:00:00', title: 'moved' }))

    expect($agendaEvents.get()).toHaveLength(2)
    expect($agendaEvents.get().map(e => e.id)).toEqual(['a', 'b'])
    expect($agendaEvents.get()[1].title).toBe('moved')
  })

  it('exposes pending rows and their count', () => {
    setAgendaEvents([
      event({ id: 'a', start_at: '2026-08-25T08:00:00' }),
      event({ id: 'b', start_at: '2026-08-25T09:00:00', status: 'pending' })
    ])

    expect($agendaPending.get().map(e => e.id)).toEqual(['b'])
    expect($agendaPendingCount.get()).toBe(1)
  })

  it('falls back to the first event when nothing is selected', () => {
    setAgendaEvents([
      event({ id: 'a', start_at: '2026-08-25T08:00:00' }),
      event({ id: 'b', start_at: '2026-08-25T09:00:00' })
    ])

    expect($agendaSelected.get()?.id).toBe('a')

    setAgendaSelectedId('b')
    expect($agendaSelected.get()?.id).toBe('b')
  })

  it('clears the selection when the selected row is removed', () => {
    setAgendaEvents([
      event({ id: 'a', start_at: '2026-08-25T08:00:00' }),
      event({ id: 'b', start_at: '2026-08-25T09:00:00' })
    ])
    setAgendaSelectedId('b')
    removeAgendaEvent('b')

    expect($agendaSelectedId.get()).toBeNull()
    expect($agendaSelected.get()?.id).toBe('a')
  })

  it('selecting a stale id falls back instead of rendering nothing', () => {
    setAgendaEvents([event({ id: 'a', start_at: '2026-08-25T08:00:00' })])
    setAgendaSelectedId('gone')

    expect($agendaSelected.get()?.id).toBe('a')
  })
})
