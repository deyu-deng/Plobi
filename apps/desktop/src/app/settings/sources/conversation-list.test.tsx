import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConversationsPayload } from './api'
import { $rosterFold } from './collapse'
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
  // 这一份词表是 `i18n/en.ts::settings.sources.conversations` 的镜像（词改了这里也要改，
  // 否则测试在护一组已经不上屏的话）。`groups` 那两词是大折叠共用的，这里一起给。
  WORDS: {
    collect: 'Collect',
    empty: 'No conversations match.',
    exclude: 'Skip',
    excludedOf: '{n} turned off',
    filter: 'Filter',
    groups: { collapse: 'Collapse', expand: 'Expand' },
    kinds: { group: 'Groups', official: 'Official accounts', private: 'Direct chats' },
    loading: 'Reading…',
    summary: '{n} conversations · {excluded} excluded',
    unnamed: 'No name found (this is the raw ID)',
    unavailable: 'This list could not be read just now',
    unreadable: 'Could not read the conversation list.',
    writeFailed: 'That did not save.'
  }
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({ t: { settings: { sources: { conversations: WORDS, groups: WORDS.groups } } } })
}))

// 组件的话取自 `settings.sources` 那一片（类型由 `useI18n` 推），测试给一份真的，
// 不给空对象——空对象会让"界面上那句话到底从哪来"这件事在这一档里消失。
const labels = { conversations: WORDS, groups: WORDS.groups } as never

function ok(conversations: ConversationsPayload['conversations']): ConversationsPayload {
  return { conversations, reason: '', state: 'ok' }
}

/**
 * 三组各一条样本，形状就是后端那一手给的（`chatlog/__init__.py::_conversations`）。
 *
 * `GH` 那条刻意是**查不到名字**的样子：`name` 就是会话号、`resolved: false`——
 * 他 10-26 说的"全是乱码"在数据里长这样，测试就得照着这一种钉住界面怎么说它。
 */
const GH = { id: 'gh_a', kind: 'official', last_at: '2026-10-21T08:00:00+08:00', name: 'gh_a', resolved: false, status: 'pending' }
const PERSON = { id: 'p-1', kind: 'private', last_at: '2026-10-20T09:00:00+08:00', name: '李四', resolved: true, status: 'excluded' }
const ROOM = { id: 'room-1', kind: 'group', last_at: '2026-10-26T15:48:03+08:00', name: '工程组', resolved: true, status: 'pending' }

// 这座面板的测试一次 render 就够；不清档会让下一条用例在**两次 render** 的 DOM 里找同一句话，
// 报成"找到多个元素"——那是测试自己的形状，不是面板的。
afterEach(() => {
  cleanup()
})

beforeEach(() => {
  vi.clearAllMocks()
  // 折叠状态是模块级的那只 atom（本机持久化），不清就会从上一条用例带进下一条：
  // 上一轮折过的组在这一轮一 render 就是收着的，症状是"别针脚全乱了"。
  $rosterFold.set({})
  getConversations.mockResolvedValue(ok([ROOM, PERSON, GH]))
})

describe('the conversation panel', () => {
  it('splits the list into the three groups he named, with counts on top', async () => {
    render(<ConversationList labels={labels} source="dingtalk" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    expect(screen.getByTestId('conversations-dingtalk-summary').textContent)
      .toBe('3 conversations · 1 excluded')
    expect(screen.getByTestId('conversations-dingtalk-group-group')).toBeTruthy()
    expect(screen.getByTestId('conversations-dingtalk-group-private').textContent).toContain('Direct chats')
    expect(screen.getByTestId('conversations-dingtalk-group-official').textContent).toContain('Official accounts')
    expect(getConversations).toHaveBeenCalledWith('dingtalk')
  })

  it('draws the groups in a fixed order, not the order the source happened to return', async () => {
    getConversations.mockResolvedValue(ok([GH, PERSON, ROOM]))
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    // 只数那三段本身（`-toggle` 那颗按钮的 testid 也是这个前缀，正则不严就会把按钮一起数进来）。
    const sections = screen.queryAllByTestId(/^conversations-wechat-group-(group|private|official)$/)

    expect(sections.map(node => node.getAttribute('data-testid')))
      .toEqual(['conversations-wechat-group-group', 'conversations-wechat-group-private',
        'conversations-wechat-group-official'])
  })

  it('does not draw an empty group for a kind this source has none of', async () => {
    getConversations.mockResolvedValue(ok([ROOM, PERSON]))
    render(<ConversationList labels={labels} source="dingtalk" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    // 钉钉那侧没有"公众号"这一族 ⇒ 不该画出一段空组（空分类整块不画，与大折叠同一条判据）。
    expect(screen.queryByTestId('conversations-dingtalk-group-official')).toBeNull()
  })

  it('names how many are turned off only in the group that has any', async () => {
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    // 关过一扇的那一组写出来；一扇都没关的不写（178 个灰点不是信息，是噪声）。
    expect(screen.getByTestId('conversations-wechat-group-private').textContent).toContain('1 turned off')
    expect(screen.getByTestId('conversations-wechat-group-group').textContent).not.toContain('turned off')
  })

  it('sorts each group newest-first and puts an absolute minute on the row', async () => {
    const OLD = { ...ROOM, id: 'room-2', last_at: '2020-03-04T05:06:07+08:00', name: '老群' }

    getConversations.mockResolvedValue(ok([OLD, ROOM]))
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    const rows = screen.getByTestId('conversations-wechat-rows-group').textContent ?? ''

    expect(rows.indexOf('工程组')).toBeLessThan(rows.indexOf('老群'))
    // 分钟级绝对时间：167 行里"3 小时前"要心算，而名单要问的就是"哪一扇还活着"。
    expect(screen.getByTestId('conversation-time-room-1').textContent).toMatch(/^10-26 15:48$/)
    expect(screen.getByTestId('conversation-time-room-2').textContent).toMatch(/^2020-03-04$/)
  })

  it('draws no time at all when the source did not report one', async () => {
    getConversations.mockResolvedValue(ok([{ ...ROOM, last_at: '' }]))
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    // 空是"这条信源没给这一格"，不许拿"现在"或"很久以前"去顶一个读数。
    expect(screen.queryByTestId('conversation-time-room-1')).toBeNull()
  })

  it('says outright which row is an id rather than a name', async () => {
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    const unnamed = screen.getByTestId('conversation-row-gh_a').querySelector('span')
    const named = screen.getByTestId('conversation-row-room-1').querySelector('span')

    expect(unnamed?.getAttribute('title')).toContain('No name found')
    expect(unnamed?.className).toContain('text-(--ui-text-tertiary)')
    // 查到名字的那些行不挂这句——每一行都自证不清就成了噪声。
    expect(named?.getAttribute('title')).toBe('工程组')
    expect(named?.className).not.toContain('text-(--ui-text-tertiary)')
  })

  it('is one switch per row: turning it off writes "excluded"', async () => {
    setConversationStatus.mockResolvedValue(ok([{ ...ROOM, status: 'excluded' }, PERSON, GH]))
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
    // 行上不再那颗文字按钮（他 10-26：「每个条目旁边给一个开关就行了」）。
    expect(screen.queryByRole('button', { name: 'Skip' })).toBeNull()

    fireEvent.click(screen.getByRole('switch', { name: '工程组 — Collect' }))

    await waitFor(() => expect(setConversationStatus).toHaveBeenCalledWith('wechat', 'room-1', 'excluded'))
    await waitFor(() => expect(screen.getByTestId('conversations-wechat-summary').textContent)
      .toBe('3 conversations · 2 excluded'))
  })

  it('brings a skipped conversation back to the default, not to "approved"', async () => {
    setConversationStatus.mockResolvedValue(ok([ROOM, { ...PERSON, status: 'pending' }, GH]))
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('李四')).toBeTruthy())
    // 关着的开关，名字里说的是"这一扇是不采"——点开它就是回到默认（采）。
    expect(screen.getByRole('switch', { name: '李四 — Skip' }).getAttribute('data-state')).toBe('unchecked')

    fireEvent.click(screen.getByRole('switch', { name: '李四 — Skip' }))

    // 「恢复采」= 把那行决定抹掉（pending），不是写一条 known——名册因此不会长满没人管的行。
    await waitFor(() => expect(setConversationStatus).toHaveBeenCalledWith('wechat', 'p-1', 'pending'))
  })

  it('collapses a group without writing a single decision', async () => {
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())

    fireEvent.click(screen.getByTestId('conversations-wechat-group-group-toggle'))

    await waitFor(() => expect(screen.queryByTestId('conversation-row-room-1')).toBeNull())
    // 折叠是显示层的临时形状：一写库的手都没动过（动了就等于"折一下改了采集范围"）。
    expect(setConversationStatus).not.toHaveBeenCalled()
    expect(screen.getByTestId('conversations-wechat-group-group-toggle').getAttribute('aria-expanded'))
      .toBe('false')
    // 别的组不受牵连——键是 `信源.类别` 两级，微信折了不该把钉钉也折了。
    expect(screen.getByTestId('conversation-row-p-1')).toBeTruthy()

    fireEvent.click(screen.getByTestId('conversations-wechat-group-group-toggle'))

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())
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

    await waitFor(() => expect(screen.getByTestId('conversations-dingtalk-empty')).toBeTruthy())
    expect(screen.queryByTestId('conversations-dingtalk-unavailable')).toBeNull()
  })

  it('does not pretend a failed write went through', async () => {
    setConversationStatus.mockRejectedValue(new Error('boom'))
    render(<ConversationList labels={labels} source="wechat" />)

    await waitFor(() => expect(screen.getByText('工程组')).toBeTruthy())

    fireEvent.click(screen.getByRole('switch', { name: '工程组 — Collect' }))

    // 写失败 ⇒ 那一行还是原样，并说那一句"没改成"。悄悄画成"已排除"
    // 就是让用户以为它关了，而下一轮它照样进来。
    await waitFor(() => expect(screen.getByTestId('conversations-wechat-write-failed')).toBeTruthy())
    expect(screen.getByTestId('conversations-wechat-summary').textContent)
      .toBe('3 conversations · 1 excluded')
  })
})
