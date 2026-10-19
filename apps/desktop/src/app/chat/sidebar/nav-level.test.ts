import { describe, expect, it } from 'vitest'

import { hidesAgentSessions, sidebarNavForLevel } from './nav-level'

const NAV = [
  { id: 'new-session', label: 'new' },
  { id: 'skills', label: 'skills' },
  { id: 'messaging', label: 'messaging' },
  { id: 'artifacts', label: 'artifacts' }
]

describe('sidebarNavForLevel (裁定 38/39 终态：纯 Agents 左栏)', () => {
  it('L1: Skills / Messaging / Artifacts', () => {
    const items = sidebarNavForLevel(NAV, { l1: true, l2Id: null })

    expect(items.map(item => item.id)).toEqual(['skills', 'messaging', 'artifacts'])
  })

  it('L2: Messaging / Artifacts, no Skills', () => {
    const items = sidebarNavForLevel(NAV, { l1: false, l2Id: 'agenda' })

    expect(items.map(item => item.id)).toEqual(['messaging', 'artifacts'])
  })

  it('mainline/overlay pages: same rail — the legacy new-session entry is gone everywhere', () => {
    const items = sidebarNavForLevel(NAV, { l1: false, l2Id: null })

    expect(items.map(item => item.id)).toEqual(['skills', 'messaging', 'artifacts'])
  })

  it('does not mutate the input array', () => {
    const before = NAV.map(item => item.id)

    sidebarNavForLevel(NAV, { l1: true, l2Id: null })

    expect(NAV.map(item => item.id)).toEqual(before)
  })
})

describe('hidesAgentSessions (WP-AGENT-MOUTH, 裁定 38/39)', () => {
  it('an Agent row never expands into a session drawer — L1 and L2 alike', () => {
    expect(hidesAgentSessions('l1')).toBe(true)
    expect(hidesAgentSessions({ l2: 'agenda' })).toBe(true)
  })

  it('false on the mainline (it is not an agent rail)', () => {
    expect(hidesAgentSessions(null)).toBe(false)
    expect(hidesAgentSessions(undefined)).toBe(false)
  })
})
