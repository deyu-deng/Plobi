// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { zh } from '@/i18n/zh'
import {
  $monitorFace,
  $monitorPeekOpen,
  $monitorSlotMode,
  $monitorSlotVisible,
  CHAT_CARD_NARROW_WIDTH,
  setChatCardWidth
} from '@/store/layout'

import { MonitorEntryButton } from './monitor-entry-button'
import { chatCardFaceDefsFor, createSlotRegistry } from './slot-registry'

/**
 * 抬头右上角那三枚入口**共用的那一个组件**（用户 11-19 17:0x：
 * 「完全复用同样的组件。不要写三套代码。」）。
 *
 * 这一档钉的是**一件组件**的行为：形状一样、按下态一样、点下去那件事一样。
 *
 * **11-25 更新（裁定 239 第四刀）**：那一列从 **slot 声明表**派生 ⇒ 这里的「那一枚」
 * 是**声明表算出来的**，不是手抄的 id。
 */
const FACES = chatCardFaceDefsFor(createSlotRegistry(), zh)

function Entry({ id }: { id: string }) {
  const face = FACES.find(item => item.id === id)

  if (!face) {
    throw new Error(`声明表里没有 ${id} 那一枚`)
  }

  return <MonitorEntryButton face={face} />
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

describe('那一个入口组件', () => {
  it('三枚走同一件形状（28px 方块、零描边）', () => {
    render(
      <>
        <Entry id="dossier" />
        <Entry id="pending" />
        <Entry id="mind-pending" />
      </>
    )

    const buttons = screen.getAllByRole('button')

    expect(buttons).toHaveLength(3)

    for (const button of buttons) {
      expect(button.getAttribute('data-top-bar-icon')).toBe('')
      expect(button.className).toContain('size-7')
      expect(button.className).toContain('rounded-md')
    }
  })

  it('只有开着的那一枚算按下', () => {
    render(
      <>
        <Entry id="dossier" />
        <Entry id="pending" />
      </>
    )

    const [dossier, pending] = screen.getAllByRole('button')

    expect(dossier.getAttribute('aria-pressed')).toBe('false')
    expect(pending.getAttribute('aria-pressed')).toBe('false')

    fireEvent.click(pending)

    expect(pending.getAttribute('aria-pressed')).toBe('true')
    expect(dossier.getAttribute('aria-pressed')).toBe('false')
  })

  it('点它 ⇒ 换到那一面；再点 ⇒ 关掉', () => {
    render(<Entry id="pending" />)

    fireEvent.click(screen.getByRole('button'))

    expect($monitorFace.get()).toBe('pending')
    expect($monitorSlotVisible.get()).toBe(true)

    fireEvent.click(screen.getByRole('button'))

    expect($monitorSlotVisible.get()).toBe(false)
  })

  it('窄形态：按下的口径看贴头浮层，不看那张卡本体', () => {
    setChatCardWidth(CHAT_CARD_NARROW_WIDTH - 1)
    render(<Entry id="pending" />)

    const button = screen.getByRole('button')

    expect(button.getAttribute('aria-pressed')).toBe('false')

    fireEvent.click(button)

    expect($monitorPeekOpen.get()).toBe(true)
    expect(button.getAttribute('aria-pressed')).toBe('true')
  })
})
