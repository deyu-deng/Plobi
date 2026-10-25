// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fetchDiaryIndex = vi.fn()
const fetchDiaryEntry = vi.fn()
const saveDiaryEntry = vi.fn()

vi.mock('./diary-api', () => ({
  fetchDiaryIndex: () => fetchDiaryIndex(),
  fetchDiaryEntry: (date: string) => fetchDiaryEntry(date),
  saveDiaryEntry: (date: string, text: string) => saveDiaryEntry(date, text)
}))

import { DiaryPane } from './diary-pane'

function renderPane() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <DiaryPane />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  fetchDiaryIndex.mockReset()
  fetchDiaryEntry.mockReset()
  saveDiaryEntry.mockReset()
  fetchDiaryIndex.mockResolvedValue({
    root: 'X:/vault/diary',
    today: '2026-10-24',
    entries: [{ date: '2026-10-24', weekday: 'Saturday', filename: '2026-10-24-Saturday.md', chars: 12, updated: '2026-10-24' }]
  })
  fetchDiaryEntry.mockResolvedValue({
    date: '2026-10-24',
    weekday: 'Saturday',
    filename: '2026-10-24-Saturday.md',
    exists: true,
    text: '# 今天\n\n正文一句。\n',
    updated: '2026-10-24'
  })
})

afterEach(cleanup)

describe('DiaryPane', () => {
  it('mounts the rich editor with the stored markdown rendered', async () => {
    renderPane()

    await waitFor(() => expect(screen.getByText('正文一句。')).toBeTruthy())
    expect(screen.getByRole('heading', { name: '今天' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Bold' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Quote' })).toBeTruthy()
  })

  it('keeps Save disabled until the text changes', async () => {
    renderPane()

    const save = await screen.findByRole('button', { name: 'Save' })
    expect((save as HTMLButtonElement).disabled).toBe(true)
  })

  it('reads the entry for the day shown in the header', async () => {
    renderPane()
    await waitFor(() => expect(fetchDiaryEntry).toHaveBeenCalled())
    expect(fetchDiaryEntry.mock.calls[0][0]).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})
