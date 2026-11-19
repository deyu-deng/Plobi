// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  $monitorFace,
  $monitorPeekOpen,
  $monitorSlotMode,
  $monitorSlotVisible,
  CHAT_CARD_NARROW_WIDTH,
  setChatCardWidth
} from '@/store/layout'

import { chatTopBarRailEntries } from './chat-top-bar'

/**
 * 对话卡右上角那一列：**三枚入口是同一个组件、同一个行为**。
 *
 * 用户 11-19 17:0x：「**三枚入口现在还是没有统一…UX也完全一致。要和现在档案的完全一致。**」
 * 「**完全复用同样的组件。不要写三套代码。只是容器内部的信息和UI细节不同而已。**」
 *
 * 这一档钉的是**行为**（点下去 store 变成什么样）与**形状**（三枚走同一件），
 * 不是源码文字。
 */
function entries() {
  return chatTopBarRailEntries({ edgeControls: {} })
}

function entry(id: string) {
  const found = entries().find(item => item.id === id)

  if (!found) {
    throw new Error(`注册表里没有 ${id} 那项`)
  }

  return found
}

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

    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))

    expect($monitorFace.get()).toBe('pending')
    expect($monitorSlotVisible.get()).toBe(true)
  })

  it('再点同一枚 ⇒ 关掉那一张卡', () => {
    render(<>{entry('pending').render()}</>)

    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))
    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))

    expect($monitorSlotVisible.get()).toBe(false)
  })

  it('点另一枚 ⇒ 换面，不是关掉', () => {
    render(
      <>
        {entry('pending').render()}
        {entry('mind-pending').render()}
      </>
    )

    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))
    fireEvent.click(screen.getByRole('button', { name: /写脑|Mind|node|nod/i }))

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

    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))

    expect(screen.getByRole('button', { name: /待放行|Pending/ }).getAttribute('aria-pressed')).toBe('true')
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
