/**
 * 待批那一屏的三件事（WP-MIND-PENDING-CONTENT）：看见正文、三枚图标、评论入账。
 *
 * 屏幕上的判据只能在这一层钉：store 的单测证明不了「人看得见什么」，
 * 后端那档证明不了「按钮在不在、点了发什么」。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { MindPendingOverlay } = await import('./mind-pending-overlay')

const { $mindPending, $mindPendingOpen } = await import('@/store/mind-pending')
const { en } = await import('@/i18n/en')

const ITEM = {
  id: 'p1',
  kind: 'mind_plan',
  paths: ['wiki/daily/2026-11-16.md'],
  summary: '1 项 · abc123def456',
  expired: false,
  changes: [
    {
      mode: 'overwrite',
      path: 'wiki/daily/2026-11-16.md',
      content: '# 日程摘要\n正文第一行，要点头的人得看得见。'
    }
  ]
}

const listing = (over: Record<string, unknown> = {}) => ({
  auto: false,
  count: 1,
  expired_count: 0,
  gate: true,
  items: [ITEM],
  pending_feedback: [],
  ...over
})

describe('mind pending overlay', () => {
  let api: ReturnType<typeof vi.fn>

  beforeEach(() => {
    api = vi.fn(async ({ path }: { path: string }) => (path.endsWith('/comment') ? listing({ ok: true }) : listing()))
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = { api }
    $mindPending.set({ auto: false, count: 0, expired_count: 0, gate: true, items: [] })
    $mindPendingOpen.set(true)
  })

  afterEach(() => {
    cleanup()
    $mindPendingOpen.set(false)
    delete (window as unknown as { plobiDesktop?: unknown }).plobiDesktop
  })

  it('shows the actual text that would be written, not just a file name', async () => {
    render(<MindPendingOverlay />)

    expect(await screen.findByText(/正文第一行/)).toBeTruthy()
    expect(screen.getByText('wiki/daily/2026-11-16.md · overwrite')).toBeTruthy()
  })

  it('offers approve, reject and comment as three separate buttons', async () => {
    render(<MindPendingOverlay />)

    await screen.findByText(/正文第一行/)
    expect(screen.getByRole('button', { name: en.mindPending.approve })).toBeTruthy()
    expect(screen.getByRole('button', { name: en.mindPending.reject })).toBeTruthy()
    expect(screen.getByRole('button', { name: en.workbench.office.note })).toBeTruthy()
  })

  it('sends the comment to the paper itself and approves nothing', async () => {
    render(<MindPendingOverlay />)

    fireEvent.click(await screen.findByRole('button', { name: en.workbench.office.note }))
    const box = screen.getByPlaceholderText(en.workbench.office.notePlaceholder)
    fireEvent.change(box, { target: { value: '以后这类先给我看一眼' } })
    fireEvent.click(screen.getByRole('button', { name: en.common.save }))

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith({
        body: { comment: '以后这类先给我看一眼' },
        method: 'POST',
        path: '/api/memory/pending/p1/comment'
      })
    )
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ path: '/api/memory/pending/p1/approve' }))
  })

  it('keeps an expired paper visible and refuses to approve it', async () => {
    api.mockImplementation(async ({ path }: { path: string }) =>
      path.endsWith('/comment') ? listing({ ok: true }) : listing({ expired_count: 1, items: [{ ...ITEM, expired: true }] })
    )

    render(<MindPendingOverlay />)

    const approve = await screen.findByRole('button', { name: en.mindPending.approve })
    expect((approve as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(en.mindPending.expired)).toBeTruthy()
  })
})
