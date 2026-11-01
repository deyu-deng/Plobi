/**
 * 右栏详情（用户 10-31）：点块之后就地看到这条日程，并且能直接改。
 *
 * 这里只钉三件右栏特有的事：字段确实渲染出来了、返回能回去、点附件走的是
 * 「当前右栏开页签」而不是甩给系统浏览器。动作本身（勾清单/确认/删除）走的是
 * `app/agenda/actions`，看板那份测试已经覆盖。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))
vi.mock('@/lib/desktop-fs', () => ({ selectDesktopPaths: vi.fn() }))
vi.mock('@/plobi', () => ({
  addAgendaAttachment: vi.fn(),
  confirmAgendaEvent: vi.fn(),
  createAgendaEvent: vi.fn(),
  deleteAgendaEvent: vi.fn(),
  dismissAgendaEvent: vi.fn(),
  removeAgendaAttachment: vi.fn(),
  updateAgendaEvent: vi.fn()
}))

import { RailEventDetail } from './event-detail'

function event(overrides: Partial<AgendaEvent> = {}): AgendaEvent {
  const now = '2026-10-31T00:00:00Z'

  return {
    attachments: [],
    confirm_seq: null,
    created_at: now,
    details: [],
    end_at: '2026-10-31T12:00:00',
    id: 'e1',
    kind: 'task',
    location: '教三-201',
    notes: '别忘带 U 盘',
    source: 'manual',
    start_at: '2026-10-31T09:00:00',
    status: 'confirmed',
    title: '基础部分实验报告',
    updated_at: now,
    ...overrides
  }
}

describe('RailEventDetail', () => {
  afterEach(() => {
    cleanup()
  })

  it('把这条日程的字段就地画出来（不用跳去别的页）', () => {
    render(
      <RailEventDetail
        event={event({ details: [{ done: false, id: 'd1', text: '数据整理' }] })}
        onBack={() => {}}
      />
    )

    expect(screen.getByTestId('rail-event-detail')).toBeTruthy()
    expect(screen.getByText('基础部分实验报告')).toBeTruthy()
    expect(screen.getByText('教三-201')).toBeTruthy()
    expect(screen.getByText('数据整理')).toBeTruthy()
    expect(screen.getByText('别忘带 U 盘')).toBeTruthy()
  })

  it('返回条能回到时间轴', () => {
    const onBack = vi.fn()

    render(<RailEventDetail event={event()} onBack={onBack} />)
    fireEvent.click(screen.getByTestId('rail-event-back'))

    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('点附件交给调用方开页签，不甩给系统浏览器', () => {
    const onOpenAttachment = vi.fn()
    const openExternal = vi.fn()

    vi.stubGlobal('plobiDesktop', { openExternal })

    try {
      render(
        <RailEventDetail
          event={event({
            attachments: [{ added_at: '2026-10-31T00:00:00', id: 'a1', name: '报告.docx', path: 'D:/x/报告.docx', size: 1024 }]
          })}
          onBack={() => {}}
          onOpenAttachment={onOpenAttachment}
        />
      )

      fireEvent.click(screen.getByText('报告.docx'))

      expect(onOpenAttachment).toHaveBeenCalledWith(
        expect.objectContaining({ name: '报告.docx', path: 'D:/x/报告.docx' })
      )
      expect(openExternal).not.toHaveBeenCalled()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
