// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ChatScope } from '@/app/console/chat/scope'
import {
  $monitorFace,
  $monitorPeekOpen,
  $monitorSlotMode,
  $monitorSlotVisible,
  CHAT_CARD_NARROW_WIDTH,
  setChatCardWidth
} from '@/store/layout'

import { ChatTopBar, chatTopBarRailEntries } from './chat-top-bar'

/**
 * 对话卡右上角那一列：**三枚入口是同一个组件、同一个行为**。
 *
 * 用户 11-19 17:0x：「**三枚入口现在还是没有统一…UX也完全一致。要和现在档案的完全一致。**」
 * 「**完全复用同样的组件。不要写三套代码。只是容器内部的信息和UI细节不同而已。**」
 *
 * 这一档钉的是**行为**（点下去 store 变成什么样）与**形状**（三枚走同一件），
 * 不是源码文字。
 */
function entries(scope: ChatScope = { id: 'Aura', kind: 'agent' }) {
  return chatTopBarRailEntries({ edgeControls: {}, scope })
}

function entry(id: string) {
  const found = entries().find(item => item.id === id)

  if (!found) {
    throw new Error(`注册表里没有 ${id} 那项`)
  }

  return found
}

/**
 * 两枚入口的账都念在 aria-label 上：「收件箱」来自 `t.pendingTickets`，「待批准」来自
 * `mindPending.title`。以前那条 `/收件箱|待放行|Inbox|Pending/` 把两枚一起罩住了 ——
 * 单卡在屏上时看不出毛病，两张卡同时挂出来就撞成 "Found multiple elements"。
 * 一个选择器只认一枚，别再靠"屏上恰好只有它"兜着。
 */
const PENDING = /收件箱|Inbox/
const MIND_PENDING = /待批准|Pending approval/

beforeEach(() => {
  setChatCardWidth(CHAT_CARD_NARROW_WIDTH + 200)
  $monitorSlotMode.set('closed')
  $monitorFace.set('dossier')
  $monitorPeekOpen.set(false)
})

afterEach(() => {
  cleanup()
})

describe('对话卡右上角那一列', () => {
  it('注册表还是那四条：档案 / 待放行 / 写脑待批 / 贴边那几枚', () => {
    expect(entries().map(item => item.id)).toEqual(['dossier', 'pending', 'mind-pending', 'edge'])
  })

  it('指挥（L1）那屏**没有档案那一枚**（指挥没有档案，用户 11-19 22:3x）', () => {
    const ids = entries({ kind: 'l1' }).map(item => item.id)

    expect(ids).not.toContain('dossier')
    expect(ids).toEqual(['pending', 'mind-pending', 'edge'])
  })

  it('首席（L2）那屏照旧有档案那一枚', () => {
    expect(entries({ id: 'Aura', kind: 'agent' }).map(item => item.id)).toContain('dossier')
  })

  it('三枚入口走的是同一个组件：同一件形状、同一枚按下态', () => {
    render(
      <>
        {entries()
          .slice(0, 3)
          .map(item => <div key={item.id}>{item.render()}</div>)}
      </>
    )

    const buttons = screen.getAllByRole('button')

    expect(buttons).toHaveLength(3)

    for (const button of buttons) {
      // 同一件形状：`TopBarIconButton` 是那一列的唯一形态。
      expect(button.getAttribute('data-top-bar-icon')).toBe('')
      expect(button.className).toContain('size-7')
    }
  })

  it('点哪一枚 ⇒ 那一张卡换到那一面', () => {
    render(<>{entry('pending').render()}</>)

    fireEvent.click(screen.getByRole('button', { name: PENDING }))

    expect($monitorFace.get()).toBe('pending')
    expect($monitorSlotVisible.get()).toBe(true)
  })

  it('再点同一枚 ⇒ 关掉那一张卡', () => {
    render(<>{entry('pending').render()}</>)

    fireEvent.click(screen.getByRole('button', { name: PENDING }))
    fireEvent.click(screen.getByRole('button', { name: PENDING }))

    expect($monitorSlotVisible.get()).toBe(false)
  })

  it('点另一枚 ⇒ 换面，不是关掉', () => {
    render(
      <>
        {entry('pending').render()}
        {entry('mind-pending').render()}
      </>
    )

    fireEvent.click(screen.getByRole('button', { name: PENDING }))
    fireEvent.click(screen.getByRole('button', { name: MIND_PENDING }))

    expect($monitorFace.get()).toBe('mind-pending')
    expect($monitorSlotVisible.get()).toBe(true)
  })

  it('开着的那一枚才算是按下的', () => {
    render(
      <>
        {entry('dossier').render()}
        {entry('pending').render()}
      </>
    )

    fireEvent.click(screen.getByRole('button', { name: PENDING }))

    expect(screen.getByRole('button', { name: PENDING }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: /档案|Dossier/ }).getAttribute('aria-pressed')).toBe('false')
  })

  it('窄时点它 ⇒ 开贴头浮层，**不去动**那张卡本体', () => {
    setChatCardWidth(CHAT_CARD_NARROW_WIDTH - 1)
    render(<>{entry('dossier').render()}</>)

    fireEvent.click(screen.getByRole('button', { name: /档案|Dossier/ }))

    expect($monitorPeekOpen.get()).toBe(true)
    expect($monitorSlotVisible.get()).toBe(false)
  })

  it('窄时再点一下 ⇒ 浮层收回去', () => {
    setChatCardWidth(CHAT_CARD_NARROW_WIDTH - 1)
    render(<>{entry('dossier').render()}</>)

    fireEvent.click(screen.getByRole('button', { name: /档案|Dossier/ }))
    fireEvent.click(screen.getByRole('button', { name: /档案|Dossier/ }))

    expect($monitorPeekOpen.get()).toBe(false)
  })
})

/**
 * 顶栏那一行**横跨整卡**（用户 11-19 22:50x：「**这几个图标还是没有放到卡片的最右边呀。**」）。
 *
 * 原来那一行住在**对话区那一列**里、按钮组靠 `ml-auto` 贴**本列**的右缘 ⇒
 * 并排列钉开时那三枚入口停在并排列**左边**，离卡片最右边差了整整一列。
 * ⇒ 这一行改成**卡级**的：绝对定位跨满整卡（包含块＝对话卡本体）。
 */
describe('顶栏那一行横跨整卡', () => {
  function renderTopBar() {
    return render(<ChatTopBar edgeControls={{}} scope={{ id: 'Aura', kind: 'agent' }} />)
  }

  it('那一行是**卡级**的：绝对定位跨满整卡（不在对话区那一列的流里）', () => {
    const { container } = renderTopBar()
    const row = container.querySelector('[data-chat-topbar-row]')

    expect(row).toBeTruthy()
    expect(row?.className).toContain('absolute')
    expect(row?.className).toContain('inset-x-0')
    expect(row?.className).toContain('z-20')
  })

  it('那一行不在流里 ⇒ 底下补一个同高的占位（下面那一块不被它压住）', () => {
    const { container } = renderTopBar()
    const spacer = container.querySelector('[data-chat-topbar-spacer]')

    expect(spacer).toBeTruthy()
    expect(spacer?.className).toContain('h-(--card-header-height)')
  })

  it('那三枚入口还在同一行里、还是贴那一行的右端（`ml-auto`）', () => {
    const { container } = renderTopBar()
    const row = container.querySelector('[data-chat-topbar-row]')
    const actions = container.querySelector('[data-chat-topbar-actions]')

    expect(row?.contains(actions as Node)).toBe(true)
    expect(actions?.className).toContain('ml-auto')
    expect(container.querySelectorAll('[data-top-bar-icon]').length).toBe(3)
  })

  it('浮层默认落点认的那一行还在（`data-chat-topbar`：名字那一块的拖把手）', () => {
    const { container } = renderTopBar()

    expect(container.querySelector('[data-chat-topbar]')).toBeTruthy()
  })
})
