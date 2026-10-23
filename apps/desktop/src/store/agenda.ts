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
 * WP-PLAN-COMPLETION-ACK：完成态的**唯一真源是事件那一份**（`events.completed_at`）。
 * 计划层 10-22 已退役，夜间排程按（日, kind, 标题）覆盖同一行时也不动这一格。
 *
 * `AgendaEvent` 的类型里目前没有 `completed_at`（那档文件在别线在派名下，本刀不碰），
 * 所以这里按结构化读一次。**不是**"字段可能不存在"，是"类型还没带上"——别当兜底抄走。
 */
export const $completedEventIds = computed($agendaEvents, events => new Set(
  events
    .filter(event => Boolean((event as { completed_at?: null | string }).completed_at))
    .map(event => event.id)
))

/**
 * WP-PLAN-ACK-NO-FAKE-CHECK：服务端**答过有这些事件**的那一份 id 集合。
 *
 * 计划行带的 `event_id` 不一定真有一条事件——排程自造的 `l1:block-3`
 * 就不是事件 id，后端对它直接 `EventNotFound` → 404。画一颗点下去只会报错的勾，
 * 比不画更坏。所以面上先问一句「这颗 id 在服务端那份 events 里吗」。
 *
 * 窗口对得上才成立：`GET /api/agenda` 不带参数时默认是**今天 00:00 → 明天 23:59:59**
 * （`plobi/agenda/service.py::list_agenda`），而这一块只画今天/明天两天的计划。
 * 空集合是「服务端还没答」，不是「一条都不存在」——用的人必须把这两种分开。
 */
export const $agendaEventIds = computed($agendaEvents, events => new Set(events.map(event => event.id)))

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
