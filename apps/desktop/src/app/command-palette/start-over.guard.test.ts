/**
 * 裁定 69 「重新开始」 row, read off the palette's source instead of rendered:
 * importing `./index` drags the pet gallery, gateway hooks and theme installer
 * into a run that the suite's timing-sensitive files can't spare the CPU for.
 * Same idiom as `console/architecture.guard.test.ts`.
 */
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { beforeAll, describe, expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'

const NAV_NEW_ID = "id: 'nav-new'"

let paletteSource = ''

beforeAll(async () => {
  paletteSource = await readFile(
    join(process.cwd(), 'src', 'app', 'command-palette', 'index.tsx'),
    'utf8'
  )
})

describe('command palette 「重新开始」 row', () => {
  it('reads as starting over, never as a session, in every locale', () => {
    for (const [locale, copy] of Object.entries(TRANSLATIONS)) {
      const { detail, title } = copy.commandCenter.nav.newChat

      expect(`${title} ${detail}`, locale).not.toMatch(/session|会话|會話|セッション/)
    }
  })

  it('runs the shell restart under that label instead of routing /new', () => {
    expect(paletteSource).toContain(NAV_NEW_ID)

    const row = paletteSource.slice(paletteSource.indexOf(NAV_NEW_ID), paletteSource.indexOf('nav-terminal'))

    // `/new` carries no secretary identity: navigating there bounces the center
    // home and the level-keyed bind brings back the transcript just retired, so
    // the row must only ask the shell for a fresh conversation.
    expect(row).toContain('label: cc.nav.newChat.title')
    expect(row).toContain('run: requestFreshSession')
    expect(paletteSource).not.toContain('NEW_CHAT_ROUTE')
  })
})
