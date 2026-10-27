import { describe, expect, it } from 'vitest'

import { buildLoginCards, type AuthCatalogRow } from './quota-login-cards'

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
