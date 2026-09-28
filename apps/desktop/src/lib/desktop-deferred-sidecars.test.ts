import { beforeEach, describe, expect, it } from 'vitest'

import type { DesktopDeferredSubsystem } from '@/global'
import { $notifications } from '@/store/notifications'

import {
  deferredNoticeId,
  deferredRowLabel,
  planDeferredSubsystemNotices,
  reportDeferredSubsystems
} from './desktop-deferred-sidecars'

// R-051 — the renderer is rendering #2 of the main-process ledger. These tests
// pin the two rules the window must obey: a deferred subsystem is announced and
// never shown as success, and it never becomes a boot error (which would hold the
// boot screen open and cost the user the conversation).

function row(overrides: Partial<DesktopDeferredSubsystem> & { id: string }): DesktopDeferredSubsystem {
  // `...overrides` last on purpose: `port` above is only *derived* from the id, and
  // `name`/`enableHint` are fixed defaults, so an explicit override of any field has
  // to win over them. `id` therefore needs no explicit key — the spread carries it.
  return {
    enableHint: 'cd aigw && uv run python -m aigw start --config config.yaml',
    name: { en: 'aigw quota gateway', zh: '额度网关 aigw' },
    detail: overrides.detail ?? `${overrides.observed ?? ''} — deferred by R-047`,
    observed: overrides.observed ?? 'unreachable (no listener)',
    port: overrides.id === 'aigw' ? 8000 : 5030,
    state: overrides.state ?? 'deferred',
    timestamp: overrides.timestamp ?? 1,
    ...overrides
  } as DesktopDeferredSubsystem
}

describe('planDeferredSubsystemNotices', () => {
  it('announces each deferred row once', () => {
    const plan = planDeferredSubsystemNotices([row({ id: 'aigw' }), row({ id: 'chatlog' })], [])

    expect(plan.show.map(item => item.id)).toEqual(['aigw', 'chatlog'])
    expect(plan.dismiss).toEqual([])
  })

  it('does not re-announce a row that is already on screen', () => {
    // A chatty broadcast must not flood the notification stack (it caps at 4).
    const plan = planDeferredSubsystemNotices([row({ id: 'aigw' })], [deferredNoticeId('aigw')])

    expect(plan.show).toEqual([])
    expect(plan.dismiss).toEqual([])
  })

  it('ignores rows that are probing or ready', () => {
    const plan = planDeferredSubsystemNotices(
      [row({ id: 'aigw', state: 'ready', observed: 'healthy on :8000' }), row({ id: 'chatlog', state: 'probing' })],
      []
    )

    expect(plan.show).toEqual([])
  })

  it('withdraws the notice the moment the subsystem is actually alive', () => {
    // doctor.py rule 3 — "真活着立刻变绿". The toast must not outlive the failure.
    const plan = planDeferredSubsystemNotices([row({ id: 'aigw', state: 'ready' })], [deferredNoticeId('aigw')])

    expect(plan.dismiss).toEqual([deferredNoticeId('aigw')])
    expect(plan.show).toEqual([])
  })

  it('never withdraws a notification it does not own', () => {
    const plan = planDeferredSubsystemNotices([], ['some-other-toast', deferredNoticeId('aigw')])

    expect(plan.dismiss).toEqual([deferredNoticeId('aigw')])
  })
})

describe('reportDeferredSubsystems', () => {
  beforeEach(() => {
    $notifications.set([])
  })

  it('raises a persistent warning notice carrying the observed reason', () => {
    reportDeferredSubsystems([row({ id: 'aigw', observed: 'no usable Python interpreter' })])

    const notices = $notifications.get()

    expect(notices).toHaveLength(1)
    expect(notices[0].kind).toBe('warning')
    expect(notices[0].id).toBe(deferredNoticeId('aigw'))
    expect(notices[0].title).toContain('aigw quota gateway')
    expect(notices[0].title).toContain('8000')
    expect(notices[0].message).toContain('deferred by R-047')
  })

  it('is idempotent across repeat broadcasts', () => {
    reportDeferredSubsystems([row({ id: 'aigw' })])
    reportDeferredSubsystems([row({ id: 'aigw' })])

    expect($notifications.get()).toHaveLength(1)
  })

  it('clears the notice when the row turns ready', () => {
    reportDeferredSubsystems([row({ id: 'aigw' })])
    expect($notifications.get()).toHaveLength(1)

    reportDeferredSubsystems([row({ id: 'aigw', state: 'ready', observed: 'healthy on :8000' })])
    expect($notifications.get()).toHaveLength(0)
  })

  it('never touches the boot state, so the window still reaches chat', () => {
    // The whole point of R-051: both sidecars down, main conversation unaffected.
    reportDeferredSubsystems([row({ id: 'aigw' }), row({ id: 'chatlog' })])

    expect($notifications.get().every(notice => notice.kind !== 'error')).toBe(true)
  })

  it('does nothing when the payload carries no rows', () => {
    reportDeferredSubsystems(undefined)
    reportDeferredSubsystems([])

    expect($notifications.get()).toEqual([])
  })
})

describe('deferredRowLabel', () => {
  it('picks the tray language matching the active locale', () => {
    const subject = row({ id: 'aigw' })

    expect(deferredRowLabel(subject, 'zh')).toBe('额度网关 aigw')
    expect(deferredRowLabel(subject, 'zh-hant')).toBe('额度网关 aigw')
    expect(deferredRowLabel(subject, 'en')).toBe('aigw quota gateway')
    expect(deferredRowLabel(subject, 'ja')).toBe('aigw quota gateway')
  })
})
