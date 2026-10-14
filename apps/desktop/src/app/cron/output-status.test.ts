import { describe, expect, it } from 'vitest'

import { cronOutputView, normalizeCronOutputStatus, OUTPUT_STATUS_TEXT } from './output-status'

const WARNING_BODY = [
  '# Cron Job: evening plan',
  '',
  '**Job ID:** 54a589f0ebb7',
  '**Run Time:** 2026-10-11T20:00:34+08:00',
  '**Mode:** no_agent (script)',
  '',
  '{"warning": "no notifier configured; evening plan not delivered"}'
].join('\n')

describe('cronOutputView', () => {
  it('the 20:00 case never renders as good — that is the whole point of this slice', () => {
    const view = cronOutputView({ status: 'warning', body: WARNING_BODY, name: '2026-10-11_20-00-34.md' }, 'zh')

    expect(view.tone).not.toBe('good')
    expect(view.tone).toBe('warn')
    expect(view.label).toBe(OUTPUT_STATUS_TEXT.warning.zh)
    expect(view.body).toContain('no notifier configured')
  })

  it('an unrecognised status is bad, not optimistic', () => {
    for (const value of ['exploded', '', undefined, null, 7, { status: 'success' }]) {
      expect(normalizeCronOutputStatus(value)).toBe('unknown')
    }

    expect(cronOutputView({ status: 'exploded' }, 'en').tone).toBe('bad')
  })

  it('the four server verdicts map to four distinct tones', () => {
    expect(cronOutputView({ status: 'success' }, 'en').tone).toBe('good')
    expect(cronOutputView({ status: 'silent' }, 'en').tone).toBe('muted')
    expect(cronOutputView({ status: 'warning' }, 'en').tone).toBe('warn')
    expect(cronOutputView({ status: 'failed' }, 'en').tone).toBe('bad')
  })

  it('no output file at all reads as "not read", never as a silent success', () => {
    const view = cronOutputView(null, 'zh')

    expect(view.status).toBe('unknown')
    expect(view.tone).toBe('bad')
    expect(view.body).toBe('')
  })

  it('a truncated body says so with the total length instead of looking complete', () => {
    const view = cronOutputView({ status: 'success', body: 'x'.repeat(10), truncated: true, chars_total: 91_000 }, 'zh')

    expect(view.truncated).toBe(true)
    expect(view.charsTotal).toBe(91_000)
  })

  it('both locales have words for every verdict', () => {
    for (const status of Object.keys(OUTPUT_STATUS_TEXT)) {
      const key = status as keyof typeof OUTPUT_STATUS_TEXT

      expect(OUTPUT_STATUS_TEXT[key].zh.length).toBeGreaterThan(0)
      expect(OUTPUT_STATUS_TEXT[key].en.length).toBeGreaterThan(0)
    }
  })
})
