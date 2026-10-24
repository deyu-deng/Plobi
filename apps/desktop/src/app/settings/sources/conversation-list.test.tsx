import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConversationsPayload } from './api'
import { ConversationList } from './conversation-list'

const getConversations = vi.fn()
const setConversationStatus = vi.fn()

vi.mock('./api', () => ({
  getConversations: (id: string) => getConversations(id),
  setConversationStatus: (id: string, talker: string, status: string) => setConversationStatus(id, talker, status)
}))

// `vi.hoisted` 是必须的：`vi.mock` 的工厂在导入阶段就被执行，那时下面那行 `const` 还没跑，
// 直接在工厂里引用它就是 TDZ 报错——整片测试连渲染都到不了。
const { WORDS } = vi.hoisted(() => ({
  WORDS: {
    collect: 'Collect',
    empty: 'No conversations match.',
    exclude: 'Skip',
    filter: 'Filter',
    kindGroup: 'Group',
    kindSingle: 'Direct',
    loading: 'Reading…',
    modeExcluded: 'Excluded',
    modeKnown: 'Collecting',
    summary: '{n} conversations · {excluded} excluded',
    unavailable: 'This list could not be read just now',
    unreadable: 'Could not read the conversation list.',
    writeFailed: 'That did not save.'
  }
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({ t: { settings: { sources: { conversations: WORDS } } } })
}))

// 组件的话取自 `settings.sources` 那一片（类型由 `useI18n` 推），测试给一份真的，
// 不给空对象——空对象会让"界面上那句话到底从哪来"这件事在这一档里消失。
const labels = { conversations: WORDS } as never

function ok(conversations: ConversationsPayload['conversations']): ConversationsPayload {
  return { conversations, reason: '', state: 'ok' }
}

const ROOM = { id: 'room-1', kind: 'group', name: '工程组', status: 'pending' }
const PERSON = { id: 'p-1', kind: 'single', name: '李四', status: 'excluded' }

// 这座面板的测试一次 render 就够；不清档会让下一条用例在**两次 render** 的 DOM 里找同一句话，
// 报成"找到多个元素"——那是测试自己的形状，不是面板的。
afterEach(() => {
  cleanup()
})

beforeEach(() => {
  vi.clearAllMocks()
  getConversations.mockResolvedValue(ok([ROOM, PERSON]))
})

describe('the conversation panel', () => {
  it('lists what the source reported, with the counts on top', async () => {
    render(<ConversationList labels={labels} source="dingtalk" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    expect(screen.getByTestId('conversations-dingtalk-summary').textContent)
      .toBe('2 conversations · 1 excluded')
    expect(screen.getByText('Group')).toBeTruthy()
    expect(screen.getByText('Direct')).toBeTruthy()
    expect(getConversations).toHaveBeenCalledWith('dingtalk')
  })

  it('leaves an undecided conversation unbadged, because "not decided" is not a status', async () => {
    render(<ConversationList labels={labels} source="dingtalk" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())

    // 「没定过」＝默认采。给 178 行各挂一个徽章不是信息，是噪声。
    expect(screen.getByTestId('conversation-row-room-1').textContent).not.toContain('Collecting')
    expect(screen.getByTestId('conversation-row-p-1').textContent).toContain('Excluded')
  })

  it('writes a decision and repaints from what the backend answered', async () => {
    setConversationStatus.mockResolvedValue(ok([{ ...ROOM, status: 'excluded' }, PERSON]))
    render(<ConversationList labels={labels} source="dingtalk" />)
    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())

    fireEvent.click(screen.getAllByRole('button', { name: 'Skip' })[0])

    await waitFor(() => expect(setConversationStatus).toHaveBeenCalledWith('dingtalk', 'room-1', 'excluded'))
    await waitFor(() => expect(screen.getByTestId('conversations-dingtalk-summary').textContent)
      .toBe('2 conversations · 2 excluded'))
  })

  it('brings a skipped conversation back to the default instead of "approving" it', async () => {
    setConversationStatus.mockResolvedValue(ok([ROOM, { ...PERSON, status: 'pending' }]))
    render(<ConversationList labels={labels} source="dingtalk" />)
    await waitFor(() => expect(screen.getByText('李四')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Collect' }))

    // 「恢复采」= 把那行决定抹掉（pending），不是写一条 known——名册因此不会长满没人管的行。
    await waitFor(() => expect(setConversationStatus).toHaveBeenCalledWith('dingtalk', 'p-1', 'pending'))
  })

  it('says "could not read just now" with the source\'s own reason, not an empty list', async () => {
    getConversations.mockResolvedValue({ conversations: [], reason: 'sidecar not listening', state: 'unavailable' })
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByTestId('conversations-wechat-unavailable').textContent)
      .toContain('sidecar not listening'))
    expect(screen.queryByText('No conversations match.')).toBeNull()
  })

  it('keeps "this source has no conversations at all" a third sentence', async () => {
    getConversations.mockResolvedValue({
      conversations: [],
      reason: '这一条信源没有「会话」这一层',
      state: 'not_supported'
    })
    render(<ConversationList labels={labels} source="zdbk" />)

    await waitFor(() => expect(screen.getByTestId('conversations-zdbk-none-layer').textContent)
      .toContain('这一条信源没有「会话」这一层'))
  })

  it('shows the empty row only when the read really succeeded with nothing', async () => {
    getConversations.mockResolvedValue(ok([]))
    render(<ConversationList labels={labels} source="dingtalk" />)

    await waitFor(() => expect(screen.getByText('No conversations match.')).toBeTruthy())
    expect(screen.queryByTestId('conversations-dingtalk-unavailable')).toBeNull()
  })

  it('does not pretend a failed write went through', async () => {
    setConversationStatus.mockRejectedValue(new Error('boom'))
    render(<ConversationList labels={labels} source="dingtalk" />)
    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())

    fireEvent.click(screen.getAllByRole('button', { name: 'Skip' })[0])

    // 写失败 ⇒ 那一行还是原样，并说"这份名单现在读不到了"；悄悄画成"已排除"
    // 就是让用户以为它关了，而下一轮它照样进来。
    await waitFor(() => expect(screen.getByTestId('conversations-dingtalk-write-failed')).toBeTruthy())
    expect(screen.getByTestId('conversations-dingtalk-summary').textContent)
      .toBe('2 conversations · 1 excluded')
  })
})
