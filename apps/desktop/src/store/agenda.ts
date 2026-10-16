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

/**
 * WP-PLAN-COMPLETION-ACK：完成态的**唯一真源是事件那一份**（`events.completed_at`），
 * 不是 `plan_items`——每晚重排会把当天那份先删再插，写在那儿等于第二天又排一遍。
 * 计划那一行因此只带 `event_id` 回来查这一格，自己不存完成态。
 *
 * `AgendaEvent` 的类型里目前没有 `completed_at`（那档文件在别线在派名下，本刀不碰），
 * 所以这里按结构化读一次。**不是**"字段可能不存在"，是"类型还没带上"——别当兜底抄走。
 */
export const $completedEventIds = computed($agendaEvents, events => new Set(
  events
    .filter(event => Boolean((event as { completed_at?: null | string }).completed_at))
    .map(event => event.id)
))

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
