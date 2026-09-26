import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const createAgendaEvent = vi.hoisted(() => vi.fn())
const upsertAgendaEvent = vi.hoisted(() => vi.fn())

vi.mock('@/plobi', () => ({
  createAgendaEvent: (...args: unknown[]) => createAgendaEvent(...args)
}))
vi.mock('@/store/agenda', () => ({
  upsertAgendaEvent: (...args: unknown[]) => upsertAgendaEvent(...args)
}))
vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      console: {
        timeline: {
          addTitle: 'Title',
          addTitlePlaceholder: 'Enter title',
          addStart: 'Start',
          addCancel: 'Cancel',
          addSubmit: 'Add'
        }
      }
    }
  })
}))
vi.mock('@/components/ui/button', () => ({
  Button: ({ children, disabled, onClick }: { children: React.ReactNode; disabled?: boolean; onClick?: () => void }) =>
    <button disabled={disabled} onClick={onClick}>{children}</button>
}))

import { AddEventForm } from './add-event'

function renderForm(onClose = vi.fn()) {
  return render(<AddEventForm onClose={onClose} />)
}

describe('AddEventForm', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('disables the submit button when title is empty', () => {
    renderForm()

    const submitBtn = screen.getByRole<HTMLButtonElement>('button', { name: 'Add' })

    expect(submitBtn.disabled).toBe(true)
  })

  it('enables submit when a title is entered', () => {
    renderForm()

    const input = screen.getByPlaceholderText('Enter title')
    fireEvent.change(input, { target: { value: 'Team standup' } })

    const submitBtn = screen.getByRole<HTMLButtonElement>('button', { name: 'Add' })

    expect(submitBtn.disabled).toBe(false)
  })

  it('calls onClose when Cancel is clicked', () => {
    const onClose = vi.fn()
    renderForm(onClose)

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onClose).toHaveBeenCalledOnce()
  })

  it('creates an event and calls onClose on success', async () => {
    const onClose = vi.fn()
    const mockEvent = { id: 'evt_1', title: 'Standup', start_at: '2026-09-01T10:00', status: 'confirmed' }
    createAgendaEvent.mockResolvedValue(mockEvent)
    upsertAgendaEvent.mockReturnValue(undefined)

    renderForm(onClose)

    const input = screen.getByPlaceholderText('Enter title')
    fireEvent.change(input, { target: { value: 'Standup' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() => {
      expect(createAgendaEvent).toHaveBeenCalledOnce()
      expect(upsertAgendaEvent).toHaveBeenCalledWith(mockEvent)
      expect(onClose).toHaveBeenCalledOnce()
    })
  })

  it('shows an error message when creation fails', async () => {
    createAgendaEvent.mockRejectedValue(new Error('Server error'))

    renderForm()

    const input = screen.getByPlaceholderText('Enter title')
    fireEvent.change(input, { target: { value: 'Standup' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() => {
      expect(screen.getByText('Server error')).toBeTruthy()
    })
  })
})
