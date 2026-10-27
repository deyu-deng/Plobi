import { describe, expect, it } from 'vitest'

import { type AuthCatalogRow, buildLoginCards, cardControl, cardDetail, type LoginCard, QUOTA_CARD_INTROS } from './quota-login-cards'

function row(over: Partial<AuthCatalogRow> = {}): AuthCatalogRow {
  return {
    aggregate: true,
    id: 'cline_cli',
    installed: true,
    kind: 'cli_login',
    live: false,
    name: 'Cline',
    reason: '',
    ...over
  }
}

/**
 * The screen is a product, not a support article (user 2026-10-27): one short line
 * about what the quota IS, `+` where a click can sign it in, a switch afterwards —
 * and the reason a leg is unusable stays on the control, never in the body.
 */
describe('buildLoginCards', () => {
  it('gives every card a one-line product intro, never a paragraph', () => {
    const [card] = buildLoginCards([row()])

    expect(card.intro).toBe('Cline 账号池额度')
    expect(card.intro.length).toBeLessThan(24)
    expect(card.intro).not.toMatch(/浏览器|命令|Plobi 不代/)
  })

  it('offers the plus where a click signs it in', () => {
    expect(buildLoginCards([row({ live: false })])[0].connectable).toBe(true)
    expect(buildLoginCards([row({ kind: 'oauth', live: false })])[0].connectable).toBe(true)
  })

  it('an installed CLI nobody signed into still offers the plus', () => {
    // Measured 2026-10-26: Gemini/Qwen are on PATH and not signed in; treating
    // "installed" as "connected" hid the only way in.
    const [card] = buildLoginCards([
      row({ aggregate: false, id: 'gemini-cli', installed: true, live: false, name: 'Gemini CLI' })
    ])

    expect(card.connectable).toBe(true)
    expect(card.connected).toBe(false)
  })

  it('a signed-in leg is a switch, not a second sign-in', () => {
    const [card] = buildLoginCards([row({ live: true })])

    expect(card).toMatchObject({ connectable: false, connected: true })
  })

  it('a local-session leg has no click: the credential lives next door', () => {
    const [card] = buildLoginCards([
      row({ id: 'workbuddy', kind: 'local_session', live: true, name: 'CodeBuddy' })
    ])

    expect(card.connectable).toBe(false)
    expect(card.intro).toBe('CodeBuddy 订阅额度')
  })

  it('keeps "we did not wire it" off the body and on the control', () => {
    const [card] = buildLoginCards([
      row({ kind: 'none', login_reason: '登录命令还没核出来', name: 'Codex', reason: 'ok' })
    ])

    expect(card.connectable).toBe(false)
    expect(card.detail).toContain('还没核出来')
    // The credential verdict ("ok") is a machine token; it must not become body copy.
    expect(card.intro).not.toBe('ok')
  })

  it('a pending sign-in stops offering another click', () => {
    const [card] = buildLoginCards([row({ login: { state: 'pending' } })])

    expect(card).toMatchObject({ connectable: false, pending: true })
  })

  it('drops a row with no id so no anonymous card can render', () => {
    expect(buildLoginCards([row({ id: '' })])).toEqual([])
  })
})

/**
 * The card grammar as a decision (`Docs/specs/settings-card-system.md` §2): exactly one
 * control per row, chosen by its shape, and the explanation of anything wrong lives on
 * that control rather than in the row's text.
 */
describe('cardControl', () => {
  const cards = buildLoginCards([
    row({ id: 'antigravity', kind: 'oauth' }),
    row({ id: 'workbuddy', kind: 'local_session', live: true, name: 'CodeBuddy' }),
    row({ aggregate: false, id: 'gemini-cli', kind: 'cli_login', live: true, name: 'Gemini CLI' })
  ])

  const [signedOut, signedIn, loginOnly]: LoginCard[] = cards

  it('a signed-out software a click can sign in is a plus', () => {
    expect(cardControl(signedOut, 'unknown')).toBe('plus')
  })

  it('software already in use is a switch', () => {
    expect(cardControl(signedIn, 'unknown')).toBe('switch')
  })

  it('the user turning it off keeps the switch, unchecked', () => {
    // Their verdict outranks the hub's: the row must show the switch either way, or
    // there is no way back except clicking something that is not there.
    expect(cardControl(signedIn, 'off')).toBe('switch')
    expect(cardControl(signedIn, 'on')).toBe('switch')
  })

  it('a signed-in login-only CLI gets no control: Plobi owns no toggle for it', () => {
    expect(cardControl(loginOnly, 'unknown')).toBe('none')
  })

  it('a sign-in waiting on the browser is the one middle state a row may show', () => {
    const [pending] = buildLoginCards([row({ id: 'antigravity', kind: 'oauth', login: { state: 'pending' } })])

    expect(cardControl(pending, 'unknown')).toBe('loader')
  })

  it('a leg with nothing to click shows nothing, not a greyed-out lie', () => {
    const [wired] = buildLoginCards([row({ id: 'codex_cli', kind: 'none', name: 'Codex' })])

    expect(cardControl(wired, 'unknown')).toBe('none')
  })

  it('the reason goes on the control and the machine token stays out of it', () => {
    const detail = cardDetail(signedIn, undefined, 'Its sign-in has expired')

    expect(detail).toContain('sign-in has expired')
    // `reason: ''` here and `live` on the card: the credential verdict is never the
    // sentence, and a raw machine value must not leak into the tooltip either.
    expect(detail).not.toMatch(/\bok\b|credential_expired|no_tools/)
  })

  it('every one-liner on this screen is a line, not a paragraph', () => {
    for (const [id, intro] of Object.entries(QUOTA_CARD_INTROS)) {
      expect(intro.length, id).toBeLessThanOrEqual(20)
    }
  })
})
