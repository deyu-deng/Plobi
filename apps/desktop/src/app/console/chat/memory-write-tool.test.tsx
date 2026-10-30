/**
 * 对话里那张记忆小卡片：只有进了待确认层才出；点头/驳回打到同一个口。
 * 文案复用已有键（不新增字符串），所以这里只需证明"显示什么、点了发生什么"。
 */
import { fireEvent, render, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryWriteTool, stagedMemoryPendingId } from './memory-write-tool'

const copy = {
  console: { chat: { cardConfirmed: '已记下', cardDismissed: '不用了', cardFailed: '没成功', cardInvalid: '这条读不出来' } },
  mindPending: { approve: '点头', reject: '驳回' }
}

vi.mock('@/i18n', () => ({
  useI18n: () => ({ t: copy })
}))

describe('MemoryWriteTool', () => {
  beforeEach(() => {
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = {
      api: vi.fn(async () => ({ ok: true }))
    }
  })

  it('只认"进了待确认层"的结果', () => {
    expect(stagedMemoryPendingId(JSON.stringify({ staged: true, pending_id: 'p1' }))).toBe('p1')
    expect(stagedMemoryPendingId({ staged: false, success: true })).toBe('')
    expect(stagedMemoryPendingId('direct write ok')).toBe('')
  })

  it('显示它想记的那句话，点「点头」打到 approve 口，并换成已记下的结果', async () => {
    const view = render(
      <MemoryWriteTool args={{ content: '理论力学周三有实验' }} result={{ staged: true, pending_id: 'p1' }} />
    )
    const box = within(view.container)

    expect(box.getByText('理论力学周三有实验')).toBeTruthy()
    fireEvent.click(box.getByText('点头'))

    const api = (window as unknown as { plobiDesktop: { api: ReturnType<typeof vi.fn> } }).plobiDesktop.api
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/memory/pending/p1/approve' }))
    expect(box.getByText('已记下')).toBeTruthy()
  })
})

  it('后端说没成就别回"已记下"（同一张纸被点过两次/已过期）', async () => {
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = {
      api: vi.fn(async () => ({ ok: false }))
    }
    const view = render(<MemoryWriteTool args={{ content: 'x' }} result={{ staged: true, pending_id: 'p1' }} />)
    const box = within(view.container)

    fireEvent.click(box.getByText('点头'))

    await waitFor(() => expect(box.getByText('没成功')).toBeTruthy(), { timeout: 3000 })
  })
