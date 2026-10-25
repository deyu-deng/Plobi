import { describe, expect, it } from 'vitest'

import { formatThreadTimeGap, shouldShowTimeGap, TIME_GAP_MS } from './time-separator'

describe('shouldShowTimeGap', () => {
  it('shows a header before the first message of a stretch', () => {
    expect(shouldShowTimeGap(undefined, new Date('2026-10-24T21:37:00'))).toBe(true)
  })

  it('hides it while the conversation is continuous', () => {
    const a = new Date('2026-10-24T21:00:00')
    const b = new Date(a.getTime() + TIME_GAP_MS - 1000)
    expect(shouldShowTimeGap(a, b)).toBe(false)
  })

  it('shows it once the gap reaches ten minutes', () => {
    const a = new Date('2026-10-24T21:00:00')
    const b = new Date(a.getTime() + TIME_GAP_MS)
    expect(shouldShowTimeGap(a, b)).toBe(true)
  })

  it('never renders for a missing or invalid timestamp', () => {
    expect(shouldShowTimeGap(undefined, undefined)).toBe(false)
    expect(shouldShowTimeGap(undefined, new Date('nope'))).toBe(false)
  })
})

describe('formatThreadTimeGap', () => {
  it('formats a real time without hardcoded words', () => {
    const label = formatThreadTimeGap(new Date('2026-10-24T21:37:00'))
    expect(label.length).toBeGreaterThan(0)
    expect(label).toMatch(/\d/)
  })

  it('returns empty for a missing date', () => {
    expect(formatThreadTimeGap(undefined)).toBe('')
  })
})
