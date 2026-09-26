import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      console: {
        pendingCard: {
          number: (n: number) => `#${n}`,
          oldValue: 'Was',
          source: (src: string, snippet: string) => `${src}: ${snippet}`,
          confirm: 'Confirm',
          dismiss: 'Dismiss'
        }
      }
    }
  })
}))
vi.mock('@/components/ui/button', () => ({
  Button: ({ children, disabled, onClick }: { children: React.ReactNode; disabled?: boolean; onClick?: () => void }) =>
    <button disabled={disabled} onClick={onClick}>{children}</button>
}))

import { PendingEventCard } from './pending-event-card'

function makeEvent(overrides: Partial<AgendaEvent> = {}): AgendaEvent {
  return {
    id: 'evt_12',
    title: '组会改时间',
    start_at: '2026-09-01T14:00:00',
    end_at: null,
    status: 'pending',
    kind: 'meeting',
    source: 'wechat',
    confirm_seq: 12,
    created_at: '2026-09-01T10:00:00',
    updated_at: '2026-09-01T10:00:00',
    ...overrides
  }
}

function renderCard(event: AgendaEvent = makeEvent(), busy = false) {
  const onConfirm = vi.fn()
  const onDismiss = vi.fn()

  const result = render(
    <PendingEventCard
      busy={busy}
      event={event}
      onConfirm={onConfirm}
      onDismiss={onDismiss}
    />
  )

  return { ...result, onConfirm, onDismiss }
}

describe('PendingEventCard', () => {
  afterEach(cleanup)

  it('renders the event title', () => {
    renderCard(makeEvent())

    expect(screen.getByText('组会改时间')).toBeTruthy()
  })

  it('renders the confirm sequence number', () => {
    renderCard(makeEvent({ confirm_seq: 12 }))

    expect(screen.getByText('#12')).toBeTruthy()
  })

  it('renders the new time when there is no prev_value', () => {
    renderCard(makeEvent({ prev_value: undefined }))

    expect(screen.getByText('2026-09-01 14:00')).toBeTruthy()
  })

  it('renders old→new time when prev_value has start_at', () => {
    renderCard(makeEvent({
      start_at: '2026-09-01T14:00:00',
      prev_value: { start_at: '2026-09-01T10:00:00', title: '组会' }
    }))

    // Old time with strikethrough
    expect(screen.getByText('2026-09-01 10:00')).toBeTruthy()
    // New time
    expect(screen.getByText('2026-09-01 14:00')).toBeTruthy()
  })

  it('renders evidence snippet when present', () => {
    renderCard(makeEvent({
      evidence: { snippet: '下周二组会改到下午两点' }
    }))

    expect(screen.getByText(/wechat.*下周二组会改到下午两点/)).toBeTruthy()
  })

  it('truncates long evidence snippets at 50 chars', () => {
    const longSnippet = 'A'.repeat(60)
    renderCard(makeEvent({
      evidence: { snippet: longSnippet }
    }))

    // The truncated text should end with …
    expect(screen.getByText(/wechat.*A…$/)).toBeTruthy()
  })

  it('does not render evidence section when snippet is absent', () => {
    renderCard(makeEvent({ evidence: undefined }))

    // No source line should appear
    expect(screen.queryByText(/wechat/)).toBeNull()
  })

  it('calls onConfirm with event id when Confirm is clicked', () => {
    const { onConfirm } = renderCard(makeEvent())

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(onConfirm).toHaveBeenCalledWith('evt_12')
  })

  it('calls onDismiss with event id when Dismiss is clicked', () => {
    const { onDismiss } = renderCard(makeEvent())

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(onDismiss).toHaveBeenCalledWith('evt_12')
  })

  it('disables buttons when busy is true', () => {
    renderCard(makeEvent(), true)

    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Confirm' }).disabled).toBe(true)
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Dismiss' }).disabled).toBe(true)
  })

  it('handles null confirm_seq gracefully', () => {
    renderCard(makeEvent({ confirm_seq: null }))

    expect(screen.getByText('#0')).toBeTruthy()
  })
})
