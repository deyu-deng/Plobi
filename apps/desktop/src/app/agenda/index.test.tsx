import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $agendaError, $agendaEvents, $agendaLoading, $agendaSelectedId } from '@/store/agenda'

const notify = vi.hoisted(() => vi.fn())
const notifyError = vi.hoisted(() => vi.fn())

vi.mock('@/store/notifications', () => ({
  notify: (...args: unknown[]) => notify(...args),
  notifyError: (...args: unknown[]) => notifyError(...args)
}))

const selectDesktopPaths = vi.hoisted(() => vi.fn())

vi.mock('@/lib/desktop-fs', () => ({
  selectDesktopPaths: (...args: unknown[]) => selectDesktopPaths(...args)
}))

const getTimetable = vi.hoisted(() => vi.fn())
const previewTimetable = vi.hoisted(() => vi.fn())
const importTimetable = vi.hoisted(() => vi.fn())

vi.mock('./timetable/api', () => ({
  getTimetable: (...args: unknown[]) => getTimetable(...args),
  importTimetable: (...args: unknown[]) => importTimetable(...args),
  previewTimetable: (...args: unknown[]) => previewTimetable(...args)
}))

// The board GETs the timetable status on mount in every test; without a
// default the mocked call returns `undefined` and the mount effect throws.
// `mockClear` (vi.clearAllMocks) keeps implementations, so this survives.
getTimetable.mockResolvedValue(null)

const getAgenda = vi.hoisted(() => vi.fn())
const addAgendaAttachment = vi.hoisted(() => vi.fn())
const confirmAgendaEvent = vi.hoisted(() => vi.fn())
const createAgendaEvent = vi.hoisted(() => vi.fn())
const deleteAgendaEvent = vi.hoisted(() => vi.fn())
const dismissAgendaEvent = vi.hoisted(() => vi.fn())
const removeAgendaAttachment = vi.hoisted(() => vi.fn())
const updateAgendaEvent = vi.hoisted(() => vi.fn())

vi.mock('@/plobi', () => ({
  addAgendaAttachment: (...args: unknown[]) => addAgendaAttachment(...args),
  confirmAgendaEvent: (...args: unknown[]) => confirmAgendaEvent(...args),
  createAgendaEvent: (...args: unknown[]) => createAgendaEvent(...args),
  deleteAgendaEvent: (...args: unknown[]) => deleteAgendaEvent(...args),
  dismissAgendaEvent: (...args: unknown[]) => dismissAgendaEvent(...args),
  getAgenda: (...args: unknown[]) => getAgenda(...args),
  removeAgendaAttachment: (...args: unknown[]) => removeAgendaAttachment(...args),
  updateAgendaEvent: (...args: unknown[]) => updateAgendaEvent(...args)
}))

import { AgendaView } from './index'

/** The calendar anchors on this instant; fixtures are built around 2026-09-11. */
const NOW = new Date('2026-09-11T08:00:00')

function renderBoard() {
  return render(<AgendaView now={NOW} onClose={() => {}} />)
}

describe('AgendaView empty board (WP-A7-EMPTY)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    $agendaEvents.set([])
    $agendaLoading.set(false)
    $agendaError.set(null)
    $agendaSelectedId.set(null)
    getAgenda.mockResolvedValue([])
    getTimetable.mockResolvedValue(null)
  })

  afterEach(() => {
    cleanup()
  })

  it('shows the start–end span in the list and the sender in the detail', async () => {
    const withEnd = {
      created_at: '2026-09-10T00:00:00Z',
      end_at: '2026-09-11T11:00:00',
      id: 'e-end',
      kind: 'meeting' as const,
      source: 'wechat' as const,
      start_at: '2026-09-11T09:00:00',
      status: 'confirmed' as const,
      title: '组会',
      updated_at: '2026-09-10T00:00:00Z'
    }

    const openEnded = {
      ...withEnd,
      end_at: null,
      evidence: { sender: '张老师', snippet: '明天上午九点组会', talker_name: '车辆2502' },
      id: 'e-open',
      title: '开题讨论'
    }

    getAgenda.mockResolvedValue([withEnd, openEnded])
    $agendaEvents.set([withEnd, openEnded])
    $agendaSelectedId.set('e-open')

    renderBoard()

    // R-009: the time axis prints the real span; an unwritten end stays a point.
    expect(await screen.findByText('09:00–11:00')).toBeTruthy()
    expect(screen.getAllByText('开题讨论').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('09:00').length).toBeGreaterThanOrEqual(1)

    // The detail names who said it (evidence.sender + conversation name).
    await waitFor(() => {
      expect(screen.getByText('张老师 · 车辆2502')).toBeTruthy()
    })
    // …and the open-ended entry shows the honest end value.
    expect(screen.getAllByText('no end time').length).toBeGreaterThanOrEqual(1)
  })

  it('falls back to unknown when the extractor could not name a sender', async () => {
    const anonymous = {
      created_at: '2026-09-10T00:00:00Z',
      end_at: null,
      evidence: { snippet: '改到三点' },
      id: 'e-anon',
      kind: 'task' as const,
      source: 'wechat' as const,
      start_at: '2026-09-11T15:00:00',
      status: 'pending' as const,
      title: '时间变更',
      updated_at: '2026-09-10T00:00:00Z'
    }

    getAgenda.mockResolvedValue([anonymous])
    $agendaEvents.set([anonymous])
    $agendaSelectedId.set('e-anon')

    renderBoard()

    await waitFor(() => {
      expect(screen.getByText('Unknown')).toBeTruthy()
    })
  })

  it('editor with an end date sends end_at and not a fake start+1h', async () => {
    createAgendaEvent.mockResolvedValue({
      created_at: '2026-09-10T00:00:00Z',
      end_at: '2026-09-12T16:00:00',
      id: 'new',
      kind: 'task',
      source: 'manual',
      start_at: '2026-09-12T15:00:00',
      status: 'confirmed',
      title: 'meeting',
      updated_at: '2026-09-10T00:00:00Z'
    })

    renderBoard()
    ;(await screen.findAllByRole('button', { name: 'New entry' }))[0].click()

    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'meeting' } })
    fireEvent.change(screen.getByLabelText('Starts'), { target: { value: '2026-09-12T15:00' } })
    fireEvent.change(screen.getByLabelText('Ends'), { target: { value: '2026-09-12T16:00' } })
    fireEvent.change(screen.getByLabelText('Location'), { target: { value: '东2-101' } })
    fireEvent.change(screen.getByLabelText('Notes'), { target: { value: '带电脑' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(createAgendaEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          end_at: '2026-09-12T16:00:00',
          location: '东2-101',
          notes: '带电脑',
          start_at: '2026-09-12T15:00:00',
          title: 'meeting'
        })
      )
    })
  })

  it('editor refuses to fabricate end_at when the input is empty', async () => {
    createAgendaEvent.mockResolvedValue({
      created_at: '2026-09-10T00:00:00Z',
      end_at: null,
      id: 'open',
      kind: 'task',
      source: 'manual',
      start_at: '2026-09-12T15:00:00',
      status: 'confirmed',
      title: 'no-end',
      updated_at: '2026-09-10T00:00:00Z'
    })

    renderBoard()
    ;(await screen.findAllByRole('button', { name: 'New entry' }))[0].click()

    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'no-end' } })
    fireEvent.change(screen.getByLabelText('Starts'), { target: { value: '2026-09-12T15:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(createAgendaEvent).toHaveBeenCalled()
    })
    const call = createAgendaEvent.mock.calls.at(-1)?.[0] as Record<string, unknown>
    expect(call.end_at).toBeUndefined()
  })

  it('editor rejects an end earlier than start with a form-level error', async () => {
    renderBoard()
    ;(await screen.findAllByRole('button', { name: 'New entry' }))[0].click()

    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'oops' } })
    fireEvent.change(screen.getByLabelText('Starts'), { target: { value: '2026-09-12T15:00' } })
    fireEvent.change(screen.getByLabelText('Ends'), { target: { value: '2026-09-12T14:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('End cannot be earlier than start')).toBeTruthy()
    expect(createAgendaEvent).not.toHaveBeenCalled()
  })

  it('renders location + notes when present on the row', async () => {
    const detailed = {
      created_at: '2026-09-10T00:00:00Z',
      end_at: null,
      id: 'e-detail',
      kind: 'task' as const,
      location: '东2-101',
      notes: '带电脑',
      source: 'manual' as const,
      start_at: '2026-09-12T15:00:00',
      status: 'confirmed' as const,
      title: 'meeting',
      updated_at: '2026-09-10T00:00:00Z'
    }

    getAgenda.mockResolvedValue([detailed])
    $agendaEvents.set([detailed])
    $agendaSelectedId.set('e-detail')

    renderBoard()

    expect(await screen.findByText('东2-101')).toBeTruthy()
    expect(screen.getByText('带电脑')).toBeTruthy()
  })

  it('keeps the empty board usable without a collection entry', async () => {
    renderBoard()

    // 会话采集已从日程面板撤掉（用户 10-22：信源只进信息池，Agent 自由调用）。
    // 空板上仍要看得见「暂无日程」和新建入口。
    expect(screen.getAllByText('Nothing scheduled').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('New entry')).toBeTruthy()
  })
})

describe('AgendaView checklist + attachments (用户 10-23)', () => {
  const base = {
    confirm_seq: null,
    confirm_seq_at: null,
    created_at: '2026-09-10T00:00:00Z',
    end_at: null,
    kind: 'task' as const,
    source: 'manual' as const,
    status: 'confirmed' as const,
    updated_at: '2026-09-10T00:00:00Z'
  }

  beforeEach(() => {
    vi.clearAllMocks()
    $agendaEvents.set([])
    $agendaLoading.set(false)
    $agendaError.set(null)
    $agendaSelectedId.set(null)
    getAgenda.mockResolvedValue([])
    getTimetable.mockResolvedValue(null)
  })

  afterEach(() => {
    cleanup()
  })

  it('creates an event together with its checklist', async () => {
    createAgendaEvent.mockResolvedValue({
      ...base,
      attachments: [],
      details: [{ done: false, id: 'd1', text: '带电脑' }],
      id: 'new-check',
      start_at: '2026-09-11T15:00:00',
      title: '开会'
    })

    renderBoard()
    ;(await screen.findAllByRole('button', { name: 'New entry' }))[0].click()
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: '开会' } })
    fireEvent.change(screen.getByLabelText('Starts'), { target: { value: '2026-09-11T15:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add item' }))
    fireEvent.change(screen.getByLabelText('e.g. bring the lab report'), { target: { value: '带电脑' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(createAgendaEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          details: [expect.objectContaining({ done: false, text: '带电脑' })],
          title: '开会'
        })
      )
    })
  })

  it('toggles one checklist item through the event API', async () => {
    const event = {
      ...base,
      attachments: [],
      details: [{ done: false, id: 'd1', text: '带电脑' }],
      id: 'e-check',
      start_at: '2026-09-11T15:00:00',
      title: '开会'
    }

    updateAgendaEvent.mockResolvedValue({
      ...event,
      details: [{ done: true, id: 'd1', text: '带电脑' }]
    })
    getAgenda.mockResolvedValue([event])
    $agendaEvents.set([event])
    $agendaSelectedId.set('e-check')

    renderBoard()

    fireEvent.click(await screen.findByLabelText('带电脑'))

    await waitFor(() => {
      expect(updateAgendaEvent).toHaveBeenCalledWith('e-check', {
        details: [{ done: true, id: 'd1', text: '带电脑' }]
      })
    })
  })

  it('copies a picked file into the backend and lists it under the event', async () => {
    const event = {
      ...base,
      attachments: [],
      details: [],
      id: 'e-att',
      start_at: '2026-09-11T15:00:00',
      title: '交报告'
    }

    const withAttachment = {
      ...event,
      attachments: [
        {
          added_at: '2026-09-11T15:00:00',
          id: 'a1',
          name: '报告.pdf',
          path: 'D:/Data/AppData/plobi/home/attachments/agenda/e-att/a1-报告.pdf',
          size: 2048
        }
      ]
    }

    selectDesktopPaths.mockResolvedValue(['D:/Desktop/报告.pdf'])
    addAgendaAttachment.mockResolvedValue(withAttachment)
    getAgenda.mockResolvedValue([event])
    $agendaEvents.set([event])
    $agendaSelectedId.set('e-att')

    renderBoard()

    fireEvent.click(await screen.findByRole('button', { name: 'Add attachment' }))

    await waitFor(() => {
      expect(addAgendaAttachment).toHaveBeenCalledWith('e-att', 'D:/Desktop/报告.pdf')
    })
    expect(await screen.findByText('报告.pdf')).toBeTruthy()
    expect(screen.getByText('2.0 KB')).toBeTruthy()
  })

  it('opens an attachment through the desktop bridge and removes only the copy', async () => {
    const openExternal = vi.fn()
    const previous = (window as { plobiDesktop?: unknown }).plobiDesktop

    Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: { openExternal } })

    try {
      const event = {
        ...base,
        attachments: [
          {
            added_at: '2026-09-11T15:00:00',
            id: 'a1',
            name: '报告.pdf',
            path: 'D:/Data/AppData/plobi/home/attachments/agenda/e-att/a1-报告.pdf',
            size: 10
          }
        ],
        details: [],
        id: 'e-att',
        start_at: '2026-09-11T15:00:00',
        title: '交报告'
      }

      removeAgendaAttachment.mockResolvedValue({ ...event, attachments: [] })
      getAgenda.mockResolvedValue([event])
      $agendaEvents.set([event])
      $agendaSelectedId.set('e-att')

      renderBoard()

      fireEvent.click(await screen.findByText('报告.pdf'))

      expect(openExternal).toHaveBeenCalledWith(
        'file:///D:/Data/AppData/plobi/home/attachments/agenda/e-att/a1-%E6%8A%A5%E5%91%8A.pdf'
      )

      fireEvent.click(screen.getByRole('button', { name: 'Remove attachment' }))

      await waitFor(() => {
        expect(removeAgendaAttachment).toHaveBeenCalledWith('e-att', 'a1')
      })
      await waitFor(() => {
        expect(screen.queryByText('报告.pdf')).toBeNull()
      })
    } finally {
      if (previous) {
        Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: previous })
      } else {
        Reflect.deleteProperty(window, 'plobiDesktop')
      }
    }
  })
})

describe('AgendaView timetable import (WP-ICS-BOARD, 裁定 29.3)', () => {
  const PREVIEW = {
    calendar_name: '课程表-2026-2027秋冬',
    count: 225,
    courses: 3,
    first: '2026-09-14',
    last: '2027-01-08',
    sample: [
      { title: '高数课', start_at: '2026-09-14T08:00:00', end_at: '2026-09-14T09:40:00', location: '东2-101' },
      { title: '线代', start_at: '2026-09-14T10:00:00', end_at: null, location: '西1-203' }
    ]
  }

  beforeEach(() => {
    vi.clearAllMocks()
    $agendaEvents.set([])
    $agendaLoading.set(false)
    $agendaError.set(null)
    $agendaSelectedId.set(null)
    getAgenda.mockResolvedValue([])
    getTimetable.mockResolvedValue(null)
    selectDesktopPaths.mockResolvedValue(['C:/Users/x/ke.ics'])
    previewTimetable.mockResolvedValue(PREVIEW)
    importTimetable.mockResolvedValue({ ...PREVIEW, created: 225, unchanged: 0, updated: 0 })
  })

  afterEach(() => {
    cleanup()
  })

  async function openPreview() {
    renderBoard()

    const row = await screen.findByRole('button', { name: 'Import schedule file' })

    row.click()

    await waitFor(() => {
      expect(previewTimetable).toHaveBeenCalledWith('C:/Users/x/ke.ics')
    })
  }

  it('filters for .ics, previews the pick, and writes nothing when cancelled', async () => {
    await openPreview()

    // Filter is handed to the existing Electron dialog — no new channel.
    expect(selectDesktopPaths).toHaveBeenCalledWith({
      filters: [{ name: 'Calendar files', extensions: ['ics', 'ical'] }],
      multiple: false
    })

    // Copy is the backend's own fields, not a client-side count.
    expect(screen.getByText('课程表-2026-2027秋冬')).toBeTruthy()
    expect(screen.getByText('225 entries / 3 kinds')).toBeTruthy()
    expect(screen.getByText('2026-09-14 → 2027-01-08')).toBeTruthy()
    expect(screen.getByText('08:00–09:40 高数课 · 东2-101')).toBeTruthy()
    // A missing end time prints only the start — no invented clock.
    expect(screen.getByText('10:00 线代 · 西1-203')).toBeTruthy()

    screen.getByRole('button', { name: 'Cancel' }).click()

    await waitFor(() => {
      expect(screen.queryByText('课程表-2026-2027秋冬')).toBeNull()
    })
    expect(importTimetable).not.toHaveBeenCalled()
  })

  it('does nothing at all when the picker is cancelled', async () => {
    selectDesktopPaths.mockResolvedValue([])

    renderBoard()
    ;(await screen.findByRole('button', { name: 'Import schedule file' })).click()

    await waitFor(() => {
      expect(selectDesktopPaths).toHaveBeenCalled()
    })
    expect(previewTimetable).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Import into the board' })).toBeNull()
  })

  it('imports on confirm, reports the backend count, and refetches immediately', async () => {
    await openPreview()

    screen.getByRole('button', { name: 'Import into the board' }).click()

    await waitFor(() => {
      expect(importTimetable).toHaveBeenCalledWith('C:/Users/x/ke.ics')
    })

    await waitFor(() => {
      expect(notify).toHaveBeenCalledWith({ message: 'Imported 225 entries' })
    })

    // Once on mount, once right after the import — not after the 8s poll.
    await waitFor(() => {
      expect(getAgenda.mock.calls.length).toBeGreaterThanOrEqual(2)
    })
    await waitFor(() => {
      expect(screen.queryByText('课程表-2026-2027秋冬')).toBeNull()
    })
  })

  it('shows the imported calendar beside the row only when the backend has one', async () => {
    getTimetable.mockResolvedValue({
      calendar_name: '课程表-2026-2027秋冬',
      event_count: 225,
      imported_at: '2026-09-14T10:00:00',
      path: 'C:/Users/x/ke.ics'
    })

    renderBoard()

    expect(await screen.findByText('课程表-2026-2027秋冬 · 225 entries')).toBeTruthy()
  })

  it('surfaces a failed preview as a notification without opening the dialog', async () => {
    previewTimetable.mockRejectedValue(new Error('bad ics'))

    renderBoard()
    ;(await screen.findByRole('button', { name: 'Import schedule file' })).click()

    await waitFor(() => {
      expect(notifyError).toHaveBeenCalled()
    })
    expect(importTimetable).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Import into the board' })).toBeNull()
  })
})
