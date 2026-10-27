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
 * The user's ruling (2026-10-26): one card per software, one sentence, one control,
 * and **nothing that asks him to type**. These are the four honest states that
 * sentence can be in, and the button only exists where a click can actually help.
 */
describe('buildLoginCards', () => {
  it('offers a click only where a click opens the browser', () => {
    expect(buildLoginCards([row()])[0]).toMatchObject({
      connectable: true,
      connected: false,
      description: expect.stringContaining('浏览器')
    })

    // Signed in already → no second sign-in to offer.
    expect(buildLoginCards([row({ live: true })])[0]).toMatchObject({
      connectable: false,
      connected: true,
      description: '已连接'
    })
  })

  it('a local-session leg is never a button: the credential lives next door', () => {
    const [card] = buildLoginCards([
      row({
        id: 'workbuddy',
        kind: 'local_session',
        live: true,
        login_reason: '它用的是桌面端自己的登录',
        name: 'CodeBuddy'
      })
    ])

    expect(card.connectable).toBe(false)
    expect(card.connected).toBe(true)
    expect(card.description).toBe('它用的是桌面端自己的登录')
  })

  it('"we have not wired it" keeps the plan\'s words, never the credential verdict', () => {
    const [card] = buildLoginCards([
      row({
        kind: 'none',
        login_reason: '这一条腿的登录命令还没从它自己的 --help 里核出来',
        name: 'Codex',
        // The credential verdict for this leg is literally "ok" — printing that
        // under a card is a machine token, not an answer.
        reason: 'ok'
      })
    ])

    expect(card.connectable).toBe(false)
    expect(card.description).toContain('还没从它自己的 --help 里核出来')
    expect(card.description).not.toBe('ok')
  })

  it('an installed CLI nobody signed into still offers the click', () => {
    // Measured 2026-10-26: Gemini/Qwen are on PATH, not signed in, and the card used
    // to read "已连接" with no button because "installed" was mistaken for "live".
    const [card] = buildLoginCards([
      row({ id: 'gemini-cli', installed: true, live: false, name: 'Gemini CLI', reason: '已装，还没登录过' })
    ])

    expect(card.connectable).toBe(true)
    expect(card.connected).toBe(false)
    expect(card.description).toContain('浏览器')
  })

  it('a CLI that is not installed is not a sign-in problem', () => {
    const [card] = buildLoginCards([
      row({ name: 'Gemini CLI', reason: '没在这台机器上找到 gemini；装好之后这一条会自己回来' })
    ])

    expect(card.connectable).toBe(false)
    expect(card.description).toContain('没在这台机器上找到')
  })

  it('a live sign-in says the browser is waiting, and stops accepting clicks', () => {
    const [card] = buildLoginCards([row({ login: { state: 'pending' } })])

    expect(card.pending).toBe(true)
    expect(card.connectable).toBe(false)
    expect(card.description).toContain('浏览器里那一步')
  })

  it('an expired oauth leg says so and offers the browser again', () => {
    const [card] = buildLoginCards([row({ id: 'antigravity', kind: 'oauth', name: 'Antigravity' })])

    expect(card).toMatchObject({ connectable: true, connected: false })
    expect(card.description).toContain('过期')
  })

  it('drops a row with no id so the list cannot render an anonymous card', () => {
    expect(buildLoginCards([row({ id: '' })])).toEqual([])
  })

  it('carries "aggregate" through, because only routed legs may be written into the store', () => {
    expect(buildLoginCards([row({ aggregate: false, id: 'gemini-cli' })])[0].aggregate).toBe(false)
  })
})
