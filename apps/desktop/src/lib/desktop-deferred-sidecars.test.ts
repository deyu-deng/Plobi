import { beforeEach, describe, expect, it } from 'vitest'

import type { DesktopDeferredSubsystem } from '@/global'
import { $notifications } from '@/store/notifications'

import {
  deferredNoticeId,
  deferredRowLabel,
  deferredStatusbarCount,
  planDeferredSubsystemNotices,
  readDeferredLedgerView,
  reportDeferredSubsystems
} from './desktop-deferred-sidecars'

// R-051/R-052 — the renderer is rendering #2 of the main-process ledger, and it
// renders the two failure-looking states DIFFERENTLY:
//
//   * `red`      — attempted here and never healthy → a dismissible alarm card. A
//                  real fault has to be visible.
//   * `deferred` — postponed by R-047 (no binary, no gateway config) → NO card.
//                  It rides the existing statusbar gateway dot, whose menu still
//                  lists the reason and the enable steps.
//
// Neither may ever become boot.error (which would hold the boot screen open and
// cost the user the conversation): see
// src/app/gateway/hooks/use-gateway-boot.ts, which refuses to let a sidecar set it.

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

function redRow(overrides: Partial<DesktopDeferredSubsystem> & { id: string }): DesktopDeferredSubsystem {
  return row({
    detail: `${overrides.observed ?? 'answered 500'} — RED — real fault, not a deferral`,
    state: 'red',
    ...overrides
  })
}

describe('readDeferredLedgerView', () => {
  it('keeps red and deferred as two different lists — the branch point of the whole UI', () => {
    // Anti-regression for the bug R-048 killed on the Python side: if someone
    // folds `red` back into `deferred` (or reads both off one filter), this is the
    // assertion that fails, because the two lists are built from two states.
    const view = readDeferredLedgerView([
      row({ id: 'aigw', state: 'deferred' }),
      redRow({ id: 'chatlog' }),
      row({ id: 'gateway', state: 'ready' }),
      row({ id: 'probe', state: 'probing' })
    ])

    expect(view.deferred.map(item => item.id)).toEqual(['aigw'])
    expect(view.red.map(item => item.id)).toEqual(['chatlog'])
    expect(view.noted.map(item => item.id).sort()).toEqual(['aigw', 'chatlog'])
    expect(view.noted).not.toEqual(view.deferred)
  })

  it('notes nothing when every row is ready or still probing', () => {
    const view = readDeferredLedgerView([
      row({ id: 'aigw', state: 'ready' }),
      row({ id: 'chatlog', state: 'probing' })
    ])

    expect(view.noted).toEqual([])
    expect(deferredStatusbarCount([row({ id: 'aigw', state: 'ready' }), row({ id: 'chatlog', state: 'probing' })])).toBe(
      0
    )
  })

  it('tolerates the empty payload the boot channel can hand over', () => {
    expect(readDeferredLedgerView()).toEqual({ deferred: [], noted: [], red: [] })
    expect(deferredStatusbarCount()).toBe(0)
  })

  it('counts exactly the rows the statusbar dot speaks for', () => {
    expect(
      deferredStatusbarCount([
        row({ id: 'aigw', state: 'deferred' }),
        redRow({ id: 'chatlog' }),
        row({ id: 'gateway', state: 'ready' })
      ])
    ).toBe(2)
  })

  it('never drops the enable steps for a row it is folding into the dot', () => {
    // The chatlog enable steps must stay reachable from the detail — that is the
    // only advice the user gets on how to turn the thing on.
    const [only] = readDeferredLedgerView([
      row({
        detail:
          'no chatlog binary on this machine — deferred by R-047. ' +
          'Enable: install Go, cd tools/chatlog && go build -o bin/chatlog ./cmd/chatlog, then serve on :5030',
        enableHint: 'install Go, cd tools/chatlog && go build -o bin/chatlog ./cmd/chatlog, then serve on :5030',
        id: 'chatlog',
        state: 'deferred'
      })
    ]).noted

    expect(only?.enableHint).toContain('go build')
    expect(only?.detail).toContain('Enable: ')
  })
})

describe('planDeferredSubsystemNotices', () => {
  it('announces each RED row once', () => {
    const plan = planDeferredSubsystemNotices([redRow({ id: 'aigw' }), redRow({ id: 'chatlog' })], [])

    expect(plan.show.map(item => item.id)).toEqual(['aigw', 'chatlog'])
    expect(plan.dismiss).toEqual([])
  })

  it('raises NO card for a deferred row: a postponement is not a fault', () => {
    const plan = planDeferredSubsystemNotices([row({ id: 'aigw' }), row({ id: 'chatlog' })], [])

    expect(plan.show).toEqual([])
    expect(plan.dismiss).toEqual([])
  })

  it('does not re-announce a fault that is already on screen', () => {
    // A chatty broadcast must not flood the notification stack (it caps at 4).
    const plan = planDeferredSubsystemNotices([redRow({ id: 'aigw' })], [deferredNoticeId('aigw')])

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
    // doctor.py rule 3 — "真活着立刻变绿". The toast must not outlive the fault.
    const plan = planDeferredSubsystemNotices([row({ id: 'aigw', state: 'ready' })], [deferredNoticeId('aigw')])

    expect(plan.dismiss).toEqual([deferredNoticeId('aigw')])
    expect(plan.show).toEqual([])
  })

  it('withdraws a card for a fault that was downgraded to a deferral, without showing a new one', () => {
    // The transition main.ts now makes on boot: red -> deferred (operator removed
    // the binary). The card must go away and must NOT be replaced by a card.
    const plan = planDeferredSubsystemNotices([row({ id: 'aigw', state: 'deferred' })], [deferredNoticeId('aigw')])

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

  it('raises a persistent warning notice carrying the observed fault', () => {
    reportDeferredSubsystems([redRow({ id: 'aigw', observed: 'no usable Python interpreter' })])

    const notices = $notifications.get()

    expect(notices).toHaveLength(1)
    expect(notices[0].kind).toBe('warning')
    expect(notices[0].id).toBe(deferredNoticeId('aigw'))
    expect(notices[0].title).toContain('aigw quota gateway')
    expect(notices[0].title).toContain('8000')
    expect(notices[0].message).toContain('RED')
  })

  it('stays silent for a deferred row — no toast at all on a clean postponed boot', () => {
    // This is the user-visible half of the fix: the yellow triangle that used to
    // appear on EVERY boot came from a row that was never a fault.
    reportDeferredSubsystems([row({ id: 'chatlog', state: 'deferred', observed: 'no chatlog binary on this machine' })])

    expect($notifications.get()).toEqual([])
    // …and the dot is what carries it instead.
    expect(deferredStatusbarCount([row({ id: 'chatlog', state: 'deferred' })])).toBe(1)
  })

  it('is idempotent across repeat broadcasts', () => {
    reportDeferredSubsystems([redRow({ id: 'aigw' })])
    reportDeferredSubsystems([redRow({ id: 'aigw' })])

    expect($notifications.get()).toHaveLength(1)
  })

  it('clears the notice when the row turns ready, and the dot goes quiet with it', () => {
    reportDeferredSubsystems([redRow({ id: 'aigw' })])
    expect($notifications.get()).toHaveLength(1)

    const ready = [row({ id: 'aigw', state: 'ready', observed: 'healthy on :8000' })]

    reportDeferredSubsystems(ready)

    expect($notifications.get()).toHaveLength(0)
    expect(deferredStatusbarCount(ready)).toBe(0)
  })

  it('never touches the boot state, so the window still reaches chat', () => {
    // The whole point: both sidecars broken, main conversation unaffected.
    reportDeferredSubsystems([redRow({ id: 'aigw' }), redRow({ id: 'chatlog' })])

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
