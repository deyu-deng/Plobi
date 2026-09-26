import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

import { AgendaProposeTool } from './agenda-propose-tool'

const confirmAgendaEventAction = vi.hoisted(() => vi.fn())

vi.mock('../api', () => ({
  confirmAgendaEventAction: (...args: unknown[]) => confirmAgendaEventAction(...args)
}))

function event(overrides: Partial<AgendaEvent> = {}): AgendaEvent {
  const now = '2026-08-30T08:00:00Z'

  return {
    confirm_seq: 12,
    created_at: now,
    end_at: null,
    evidence: { snippet: '高数课改到 10:00' },
    id: 'e1',
    kind: 'class',
    prev_value: { start_at: '2026-08-31T08:00:00', title: '高数课' },
    source: 'wechat',
    start_at: '2026-08-31T10:00:00',
    status: 'pending',
    title: '高数课改期',
    updated_at: now,
    ...overrides
  }
}

describe('AgendaProposeTool', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('renders the card with sequence number, old/new values and actions', () => {
    render(<AgendaProposeTool args={event()} />)

    expect(screen.getByText('#12')).toBeTruthy()
    expect(screen.getByText('高数课改期')).toBeTruthy()
    expect(screen.getByText('was')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Ignore' })).toBeTruthy()
  })

  it('shows the invalid-payload state when args cannot be parsed', () => {
    render(<AgendaProposeTool args={{ nope: true }} />)

    expect(screen.getByText('This proposal could not be read.')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('confirms through the api layer and shows the receipt', async () => {
    confirmAgendaEventAction.mockResolvedValue({ ok: true })
    render(<AgendaProposeTool args={event()} />)

    screen.getByRole('button', { name: 'Confirm' }).click()

    await waitFor(() => {
      expect(confirmAgendaEventAction).toHaveBeenCalledWith('e1', 'confirm')
      expect(screen.getByText('Confirmed.')).toBeTruthy()
    })
  })

  it('dismisses through the api layer and shows the receipt', async () => {
    confirmAgendaEventAction.mockResolvedValue({ ok: true })
    render(<AgendaProposeTool args={event()} />)

    screen.getByRole('button', { name: 'Ignore' }).click()

    await waitFor(() => {
      expect(confirmAgendaEventAction).toHaveBeenCalledWith('e1', 'dismiss')
      expect(screen.getByText('Ignored.')).toBeTruthy()
    })
  })

  it('shows the failure state when the api layer rejects', async () => {
    confirmAgendaEventAction.mockRejectedValue(new Error('down'))
    render(<AgendaProposeTool args={event()} />)

    screen.getByRole('button', { name: 'Confirm' }).click()

    await waitFor(() => {
      expect(screen.getByText('Could not reach the agenda service. Try again.')).toBeTruthy()
    })
  })

  it('disables both actions while a request is in flight', async () => {
    let release: (() => void) | undefined
    confirmAgendaEventAction.mockReturnValue(
      new Promise(resolve => {
        release = () => resolve({ ok: true })
      })
    )
    render(<AgendaProposeTool args={event()} />)

    const confirm = screen.getByRole('button', { name: 'Confirm' }) as HTMLButtonElement
    confirm.click()

    await waitFor(() => {
      expect((screen.getByRole('button', { name: 'Ignore' }) as HTMLButtonElement).disabled).toBe(true)
    })

    release?.()
    await waitFor(() => {
      expect(screen.getByText('Confirmed.')).toBeTruthy()
    })
  })
})
