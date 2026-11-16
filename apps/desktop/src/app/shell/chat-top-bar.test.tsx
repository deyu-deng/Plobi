// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  $dossierPanelMode,
  $dossierPanelVisible,
  $dossierPeekOpen,
  CHAT_CARD_NARROW_WIDTH,
  setChatCardWidth
} from '@/store/layout'

import { chatTopBarRailEntries, dossierRailPressed } from './chat-top-bar'

/**
 * 对话卡右上角那一列里，**档案那枚图标按下去该做什么**。
 *
 * 用户 11-16 19:4x 拍板：「这一列先只做**容器查询**（宽度不够就折叠、不遮对话）」。
 * Qoder 的机制（11-08 逆向报告）：**形状由宽度决定，状态变量只管「在不在」**。
 * 窄到装不下档案列 ⇒ 档案卡折叠（不占位、不遮挡），那枚图标变成**唯一入口**，
 * 点它以贴头浮层展开；宽时它的老样子不变（点图标切换档案卡显隐）。
 *
 * 这一档钉的是**行为**（点下去 store 变成什么样），不是源码文字。
 */
function dossierEntry(narrow: boolean, peekOpen = false) {
  const entries = chatTopBarRailEntries({
    dossierLabel: '档案',
    dossierVisible: false,
    edgeControls: {},
    narrow,
    peekOpen
  })

  const dossier = entries.find(entry => entry.id === 'dossier')

  if (!dossier) {
    throw new Error('注册表里没有档案那项')
  }

  return dossier
}

beforeEach(() => {
  setChatCardWidth(CHAT_CARD_NARROW_WIDTH + 200)
  $dossierPanelMode.set('closed')
  $dossierPeekOpen.set(false)
})

afterEach(() => {
  cleanup()
})

describe('对话卡右上角那枚档案图标', () => {
  it('宽时点它 ⇒ 切档案卡本体的显隐（老样子不变）', () => {
    render(<>{dossierEntry(false).render()}</>)

    fireEvent.click(screen.getByRole('button', { name: '档案' }))

    expect($dossierPanelVisible.get()).toBe(true)
    expect($dossierPeekOpen.get()).toBe(false)
  })

  it('窄时点它 ⇒ 开贴头浮层，**不去动**档案卡本体', () => {
    render(<>{dossierEntry(true).render()}</>)

    fireEvent.click(screen.getByRole('button', { name: '档案' }))

    expect($dossierPeekOpen.get()).toBe(true)
    expect($dossierPanelVisible.get()).toBe(false)
  })

  it('窄时再点一下 ⇒ 浮层收回去', () => {
    render(<>{dossierEntry(true).render()}</>)

    fireEvent.click(screen.getByRole('button', { name: '档案' }))
    fireEvent.click(screen.getByRole('button', { name: '档案' }))

    expect($dossierPeekOpen.get()).toBe(false)
  })

  it('那枚图标「开着」的样子：窄时看浮层，宽时看档案卡', () => {
    expect(dossierRailPressed({ dossierVisible: true, narrow: false, peekOpen: false })).toBe(true)
    expect(dossierRailPressed({ dossierVisible: false, narrow: false, peekOpen: true })).toBe(false)
    expect(dossierRailPressed({ dossierVisible: false, narrow: true, peekOpen: true })).toBe(true)
    expect(dossierRailPressed({ dossierVisible: true, narrow: true, peekOpen: false })).toBe(false)
  })

  it('那一列还是注册表驱动的：档案 / 待放行 / 边缘三件', () => {
    const entries = chatTopBarRailEntries({
      dossierLabel: '档案',
      dossierVisible: false,
      edgeControls: {},
      narrow: false,
      peekOpen: false
    })

    expect(entries.map(entry => entry.id)).toEqual(['dossier', 'pending', 'edge'])
  })
})
