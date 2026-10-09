import { describe, expect, it } from 'vitest'

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { TRANSLATIONS } from '@/i18n/catalog'

// ─────────────────────────────────────────────────────────────────────────────
// 裁定 45 第②半 (§45.4, Docs/ARCH-RULINGS_2026-09-08.md): 「aigw」may be resolved at
// runtime and logged, but it must never appear in text a user reads on screen.
// `src/app/shell/model-provider-label.ts:30` already calls it "internal vocabulary
// that must never surface"; this file turns that sentence into a guard.
//
// The rule the four assertions below actually implement, stated honestly:
//
//   1. every i18n message value (all four locales, message *functions* included —
//      their source is what the rendered line is built from) must not contain `aigw`;
//   2. every STRING LITERAL in the renderer (`src/**`, non-test) must not contain
//      `aigw` — comments, identifiers and regex literals are exempt because none of
//      them can reach the DOM by themselves, and
//   3. every string literal in the main process (`electron/**`, non-test) that is not
//      part of a log call must not contain `aigw`, EXCEPT the checked-in allow-list
//      below (a ledger id, a Python module name, a filename on disk). Those are
//      machine-read identifiers per §45.4, not display copy.
//
// What this does NOT cover — said out loud instead of pretended away:
//   * a provider id/label arriving at *runtime* from the Python backend (picker rows
//     whose `name` comes over IPC). That path belongs to `humanizeProviderName`,
//     covered by ./model-provider-label.test.ts.
//   * text assembled at render time from a variable that holds `aigw` — a literal
//     scan cannot see through indirection.
//   * log output, `plobi/` Python, `doctor.py` row ids and config values: exempt by
//     ruling (「日志不是屏幕文案」), not by oversight.
// ─────────────────────────────────────────────────────────────────────────────

const DESKTOP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

const FORBIDDEN = /aigw/i

interface StringLiteral {
  /** 1-based line the literal starts on. */
  line: number
  text: string
}

/**
 * Extract string literals that sit *outside* comments. Hand-rolled left-to-right
 * scanner on purpose: a regex over the whole file would either eat the `//` inside
 * `'http://127.0.0.1:8000/v1'` as a comment or eat the comment tail as a string.
 * Line numbers are derived from the byte offset, never counted by hand.
 */
function stringLiterals(source: string): StringLiteral[] {
  const found: { start: number; text: string }[] = []
  const quotes = new Set(['"', "'", '`'])

  let index = 0

  while (index < source.length) {
    const char = source[index]

    // line comment — runs to end of line
    if (char === '/' && source[index + 1] === '/') {
      while (index < source.length && source[index] !== '\n') {
        index += 1
      }
      continue
    }

    // block comment
    if (char === '/' && source[index + 1] === '*') {
      index += 2

      while (index < source.length && !(source[index] === '*' && source[index + 1] === '/')) {
        index += 1
      }

      index += 2
      continue
    }

    if (quotes.has(char)) {
      const quote = char
      const start = index

      index += 1

      let text = ''

      while (index < source.length && source[index] !== quote) {
        if (source[index] === '\\') {
          text += source[index] + (source[index + 1] ?? '')
          index += 2
          continue
        }

        if (source[index] === '\n' && quote !== '`') {
          break
        }

        text += source[index]
        index += 1
      }

      found.push({ start, text })
      index += 1
      continue
    }

    index += 1
  }

  return found.map(literal => ({
    line: source.slice(0, literal.start).split('\n').length,
    text: literal.text
  }))
}

/** Log-call detection window: a wrapped call puts the literal a line or two below the logger. */
const LOG_CALL = /rememberLog\(|logAigw\(|console\.(log|warn|error)|logger\.|process\.stdout\.write/

function fileLines(file: string): string[] {
  return fs.readFileSync(file, 'utf8').split('\n')
}

/** Console/log lines are exempt by ruling: 「日志不是屏幕文案」 (§45.4). */
function isLogLine(lines: string[], line: number): boolean {
  return lines.slice(Math.max(0, line - 3), line).some(text => LOG_CALL.test(text))
}

/**
 * The checked-in exceptions for the main process. Every entry is an identifier the
 * machine reads — a ledger id, a Python or TypeScript module name, a filename on disk,
 * an environment variable name — never a word the UI invents for itself. The operator
 * command line that used to be the fourth entry is gone: 裁定 (2026-09-29) turned that
 * hint into a statement about what the app does by itself, so do not re-add it.
 */
const ALLOWED_LITERALS: ReadonlyArray<{ pattern: RegExp; reason: string }> = [
  { pattern: /^aigw$/, reason: 'machine-read sidecar ledger id (reportDeferredSubsystem / DEFERRED_SUBSYSTEMS key)' },
  { pattern: /^aigw-key$/, reason: 'machine-read sidecar ledger id of the key-reconcile row (KEY_RECONCILE_ROW_ID)' },
  { pattern: /^import aigw\.cli$/, reason: 'Python module name used by the interpreter liveness probe' },
  { pattern: /^aigw-token\.txt$/, reason: 'filename of the persisted OAuth token on disk' },
  { pattern: /^\.\/aigw-key-reconcile$/, reason: 'TypeScript module path the main process imports (filename on disk)' },
  { pattern: /^AIGW_API_KEY$/, reason: 'environment variable name the credential ladder reads (never display copy)' },
  { pattern: /^PLOBI_AIGW_API_KEY$/, reason: 'environment variable name the credential ladder reads (never display copy)' }
]

function allowedLiteral(text: string): boolean {
  return ALLOWED_LITERALS.some(entry => entry.pattern.test(text))
}

function sourceFiles(relativeDir: string): string[] {
  const absolute = path.join(DESKTOP_ROOT, relativeDir)

  if (!fs.existsSync(absolute)) {
    return []
  }

  const out: string[] = []

  for (const entry of fs.readdirSync(absolute, { withFileTypes: true })) {
    const full = path.join(absolute, entry.name)

    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules' && entry.name !== 'dist' && entry.name !== 'out') {
        out.push(...sourceFiles(path.relative(DESKTOP_ROOT, full)))
      }

      continue
    }

    if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(full)
    }
  }

  return out
}

function violatingLiterals(relativeDir: string, { skipLogLines }: { skipLogLines: boolean }): string[] {
  return sourceFiles(relativeDir).flatMap(file => {
    const lines = fileLines(file)

    return stringLiterals(lines.join('\n'))
      .filter(literal => FORBIDDEN.test(literal.text))
      .filter(literal => !(skipLogLines && isLogLine(lines, literal.line)))
      .filter(literal => !(skipLogLines && allowedLiteral(literal.text)))
      .map(literal => `${path.relative(DESKTOP_ROOT, file)}:${literal.line} → ${JSON.stringify(literal.text.slice(0, 90))}`)
  })
}

/** Collect every i18n message value, walking nested objects and checking functions. */
function i18nStrings(value: unknown, locale: string, trail: string[]): string[] {
  if (typeof value === 'string') {
    return [`${locale}.${trail.join('.')}: ${value}`]
  }

  if (typeof value === 'function') {
    return [`${locale}.${trail.join('.')}: ${value.toString()}`]
  }

  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, nested]) => i18nStrings(nested, locale, [...trail, key]))
  }

  return []
}

describe('裁定 45.4 guard — "aigw" must never be user-visible text', () => {
  it('the scanner flags the word inside a string literal and ignores it inside comments', () => {
    const sample = `const a = 'aigw quota hub' // aigw in a line comment\n/* aigw in a block */\nconst b = "clean"\n`
    const hits = stringLiterals(sample).filter(literal => FORBIDDEN.test(literal.text))

    expect(hits).toHaveLength(1)
    expect(hits[0]?.text).toBe('aigw quota hub')
    expect(hits[0]?.line).toBe(1)
  })

  it('no i18n message value, in any locale, contains "aigw"', () => {
    const violations = Object.entries(TRANSLATIONS)
      .flatMap(([locale, tree]) => i18nStrings(tree, locale, []))
      .filter(entry => FORBIDDEN.test(entry))

    expect(violations).toEqual([])
  })

  it('no string literal in the renderer sources contains "aigw"', () => {
    expect(violatingLiterals('src', { skipLogLines: false })).toEqual([])
  })

  it('no non-log string literal in the main process contains "aigw" beyond the machine-read allow-list', () => {
    expect(violatingLiterals('electron', { skipLogLines: true })).toEqual([])
  })
})
