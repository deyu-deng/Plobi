import { describe, expect, it } from 'vitest'

import {
  agentRoute,
  agentRouteId,
  appViewForPath,
  CONSOLE_ROUTE,
  HOME_ROUTE,
  isAgentRoute,
  isNewChatRoute,
  isOverlayView,
  isShellPageView,
  NEW_CHAT_ROUTE,
  pathnameFromRouteToken,
  routeSessionId,
  shellLevelForPath,
  shouldSyncSessionUrl,
  showsComposerCodingRow
} from './routes'

// U6 (spec §3.5): the S1 three-column console is the cold-start landing and
// the old chat home became the L2 workbench at `/agent/:id`. These assertions
// pin that inversion so a future route edit cannot silently flip it back.
describe('routes (U6 landing inversion)', () => {
  it('lands on the console at /', () => {
    expect(HOME_ROUTE).toBe('/')
    expect(appViewForPath('/')).toBe('console')
  })

  it('keeps /console as an alias for the same console view', () => {
    expect(appViewForPath(CONSOLE_ROUTE)).toBe('console')
  })

  it('routes /agent/:id to the L2 workbench', () => {
    expect(appViewForPath('/agent/agenda-secretary')).toBe('agent')
    expect(isAgentRoute('/agent/agenda-secretary')).toBe(true)
    expect(agentRouteId('/agent/agenda-secretary')).toBe('agenda-secretary')
    expect(agentRoute('a b')).toBe('/agent/a%20b')
  })

  it('keeps the fresh-draft chat at /new and never treats it as a session', () => {
    expect(NEW_CHAT_ROUTE).toBe('/new')
    expect(isNewChatRoute('/new')).toBe(true)
    expect(routeSessionId('/new')).toBeNull()
    // WP-E: /new no longer hosts a chat shell — bounce reports console, not chat.
    expect(appViewForPath('/new')).toBe('console')
    // Why this also rules out routing a 秘书「重新开始」 through /new: with no
    // secretary identity the center's `*` route gets a null `chatScope` and
    // `<Navigate to="/">` back, which re-runs the level-keyed bind effect.
    expect(shellLevelForPath('/new')).toBeNull()
  })

  it('parses session deep links but does not enable the legacy chat shell view', () => {
    expect(routeSessionId('/sess-123')).toBe('sess-123')
    // WP-E: reporting `chat` here re-enabled the preview rail on an empty
    // center (Navigate→L1), causing a full-bleed markdown flash.
    expect(appViewForPath('/sess-123')).toBe('console')
  })

  it('treats console and workbench as first-class routes, not overlays', () => {
    expect(isOverlayView('console')).toBe(false)
    expect(isOverlayView('agent')).toBe(false)
    // Genuine overlays are untouched.
    expect(isOverlayView('agenda')).toBe(true)
    expect(isOverlayView('settings')).toBe(true)
  })

  // Open full board → /agenda. Must stay an overlay view (and reserved path),
  // not fall through as a session / console bounce in the shell.
  it('keeps /agenda and /starmap as overlay routes, not sessions', () => {
    expect(appViewForPath('/agenda')).toBe('agenda')
    expect(appViewForPath('/starmap')).toBe('starmap')
    expect(routeSessionId('/agenda')).toBeNull()
    expect(routeSessionId('/starmap')).toBeNull()
  })

  it('keeps skills/messaging/artifacts as main-pane pages, not overlays', () => {
    expect(isOverlayView('skills')).toBe(false)
    expect(isOverlayView('messaging')).toBe(false)
    expect(isOverlayView('artifacts')).toBe(false)
    expect(isShellPageView('skills')).toBe(true)
    expect(isShellPageView('messaging')).toBe(true)
    expect(isShellPageView('artifacts')).toBe(true)
    expect(isShellPageView('settings')).toBe(false)
    expect(isShellPageView('console')).toBe(false)
  })

  it('does not sync session URLs on L1/L2 secretary shells (WP-E)', () => {
    expect(shouldSyncSessionUrl('/')).toBe(false)
    expect(shouldSyncSessionUrl('/agent/simulation')).toBe(false)
    expect(shouldSyncSessionUrl('/new')).toBe(true)
    expect(shouldSyncSessionUrl('/sess-123')).toBe(true)
    expect(pathnameFromRouteToken('/agent/simulation::')).toBe('/agent/simulation')
    expect(pathnameFromRouteToken('/agent/simulation:?x=1:#frag')).toBe('/agent/simulation')
  })
})

// 裁定 40 (WP-L1-CHROME): the chief secretary is not a labourer — no git
// branch/worktree strip above its mouth. The project L2 workbench keeps it, and
// so does the original session chat.
describe('showsComposerCodingRow (WP-L1-CHROME, 裁定 40)', () => {
  it('hides the coding strip on L1 (总秘书)', () => {
    expect(showsComposerCodingRow(shellLevelForPath('/'))).toBe(false)
    expect(showsComposerCodingRow(shellLevelForPath('/console'))).toBe(false)
    expect(showsComposerCodingRow('l1')).toBe(false)
  })

  it('keeps it on an L2 project workbench', () => {
    expect(showsComposerCodingRow(shellLevelForPath('/agent/agenda'))).toBe(true)
    expect(showsComposerCodingRow({ l2: 'agenda' })).toBe(true)
  })

  it('keeps it on the mainline session chat (level === null)', () => {
    expect(showsComposerCodingRow(shellLevelForPath('/sess-123'))).toBe(true)
    expect(showsComposerCodingRow(null)).toBe(true)
  })
})
