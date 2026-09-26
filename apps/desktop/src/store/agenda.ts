import { atom, computed } from 'nanostores'

import type { AgendaEvent } from '@/types/plobi'

/**
 * Agenda board state (AI-secretary M1).
 *
 * The backend owns the truth; this store is a render cache refreshed by a
 * short poll. Anything that mutates goes through `@/plobi` and then writes
 * the server's answer back here — never optimistic-only.
 */

export const $agendaEvents = atom<AgendaEvent[]>([])
export const $agendaLoading = atom(true)
export const $agendaError = atom<null | string>(null)
export const $agendaSelectedId = atom<null | string>(null)

/** Pending rows are what the user must act on, so they surface first. */
export const $agendaPending = computed($agendaEvents, events => events.filter(e => e.status === 'pending'))

export const $agendaPendingCount = computed($agendaPending, pending => pending.length)

export const $agendaSelected = computed([$agendaEvents, $agendaSelectedId], (events, selectedId) => {
  if (!selectedId) {
    return events[0] ?? null
  }

  return events.find(event => event.id === selectedId) ?? events[0] ?? null
})

function byStart(a: AgendaEvent, b: AgendaEvent): number {
  return a.start_at.localeCompare(b.start_at) || a.created_at.localeCompare(b.created_at)
}

export function setAgendaEvents(events: AgendaEvent[]): void {
  $agendaEvents.set([...events].sort(byStart))
}

/** Replace one row in place (after confirm / patch) without a full refetch. */
export function upsertAgendaEvent(event: AgendaEvent): void {
  const current = $agendaEvents.get()
  const index = current.findIndex(candidate => candidate.id === event.id)
  const next = index === -1 ? [...current, event] : current.map((item, i) => (i === index ? event : item))

  $agendaEvents.set(next.sort(byStart))
}

export function removeAgendaEvent(eventId: string): void {
  $agendaEvents.set($agendaEvents.get().filter(event => event.id !== eventId))

  if ($agendaSelectedId.get() === eventId) {
    $agendaSelectedId.set(null)
  }
}

export function setAgendaSelectedId(eventId: null | string): void {
  $agendaSelectedId.set(eventId)
}

export function setAgendaLoading(loading: boolean): void {
  $agendaLoading.set(loading)
}

export function setAgendaError(message: null | string): void {
  $agendaError.set(message)
}
