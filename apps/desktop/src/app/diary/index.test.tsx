// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DiaryEntry, DiaryIndex } from './api'

const fetchDiaryIndex = vi.fn()
const fetchDiaryEntry = vi.fn()
const saveDiaryEntry = vi.fn()
const captureDiaryLine = vi.fn()

vi.mock('./api', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchDiaryIndex: () => fetchDiaryIndex(),
  fetchDiaryEntry: (date: string) => fetchDiaryEntry(date),
  saveDiaryEntry: (date: string, text: string, base?: string) => saveDiaryEntry(date, text, base),
  captureDiaryLine: (date: string, line: string) => captureDiaryLine(date, line)
}))

import { DiaryCaptureOverlay } from './capture-overlay'
import { DiaryView } from './index'
import { shiftDay, todayISO } from './api'
import { $diaryCaptureOpen, openDiaryCapture } from '@/store/diary-capture'

const TODAY = todayISO()
const EARLIER = shiftDay(TODAY, -3)

function indexFixture(): DiaryIndex {
  return {
    root: 'X:/surface/diary',
    today: TODAY,
    entries: [
      { date: EARLIER, weekday: 'Monday', filename: `${EARLIER}-Monday.md`, chars: 120, updated: EARLIER },
      { date: TODAY, weekday: 'Tuesday', filename: `${TODAY}-Tuesday.md`, chars: 40, updated: TODAY }
    ]
  }
}

function entryFixture(date: string, text: string): DiaryEntry {
  return { date, weekday: 'X', filename: `${date}.md`, exists: true, text, updated: date }
}

function withProviders(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // 这一页头顶那枚「返回」走 `useNavigate`（用户 11-03：退回去太麻烦），所以渲染时
  // 得有一个 router——生产里它挂在 AppShell 的 Routes 下。
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </MemoryRouter>
  )
}

beforeEach(() => {
  fetchDiaryIndex.mockReset()
  fetchDiaryEntry.mockReset()
  saveDiaryEntry.mockReset()
  captureDiaryLine.mockReset()
  fetchDiaryIndex.mockResolvedValue(indexFixture())
  fetchDiaryEntry.mockImplementation((date: string) =>
    Promise.resolve(
      date === TODAY
        ? entryFixture(TODAY, '今天写的第一段。\n')
        : entryFixture(date, '往日写的一段。\n')
    )
  )
})

afterEach(cleanup)

describe('DiaryView', () => {
  it('opens today with the stored text in the editor and lists the other days', async () => {
    withProviders(<DiaryView />)

    expect(await screen.findByText('今天写的第一段。')).toBeTruthy()
    expect(screen.getByText(EARLIER.slice(5))).toBeTruthy()
    expect(fetchDiaryEntry).toHaveBeenCalledWith(TODAY)
  })

  it('头顶有一枚回对话的返回键', async () => {
    withProviders(<DiaryView />)

    const back = await screen.findByRole('button', { name: 'Back' })

    expect(back.innerHTML).toContain('codicon-arrow-left')
  })

  it('switches to another day and reads that day', async () => {
    withProviders(<DiaryView />)
    await waitFor(() => expect(screen.getByText('今天写的第一段。')).toBeTruthy())

    fireEvent.click(screen.getByText(EARLIER.slice(5)))
    await waitFor(() => expect(fetchDiaryEntry).toHaveBeenCalledWith(EARLIER))
    expect(await screen.findByText('往日写的一段。')).toBeTruthy()
  })
})

describe('DiaryCaptureOverlay', () => {
  it('saves one line into today and closes itself', async () => {
    captureDiaryLine.mockResolvedValue({
      date: TODAY,
      weekday: 'Tuesday',
      filename: `${TODAY}.md`,
      fsPath: `X:/${TODAY}.md`,
      bytes: 20,
      merged: 0,
      committed: true,
      commit: 'abc1234',
      detail: '',
      line: `- 09:15 一句话`
    })
    $diaryCaptureOpen.set(false)
    withProviders(<DiaryCaptureOverlay />)

    openDiaryCapture()
    const input = await screen.findByPlaceholderText('That thought — write it down…')
    fireEvent.change(input, { target: { value: '一句话' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(captureDiaryLine).toHaveBeenCalledWith(TODAY, '一句话'))
    await waitFor(() => expect($diaryCaptureOpen.get()).toBe(false))
  })

  it('does not save an empty line', async () => {
    $diaryCaptureOpen.set(false)
    withProviders(<DiaryCaptureOverlay />)

    openDiaryCapture()
    const input = await screen.findByPlaceholderText('That thought — write it down…')
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(captureDiaryLine).not.toHaveBeenCalled()
  })
})
