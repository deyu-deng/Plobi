import { beforeEach, describe, expect, it } from 'vitest'

import { modePref, skinPref } from './context'
import { DEFAULT_SKIN_NAME } from './presets'

/**
 * 外观是这台机器的一份，不是一个 Agent 一份（用户 11-04：「为什么有时候切换 Agent，软件
 * 外观主题会切换呀」→ 拍「这个东西肯定是 profile 的回归。我们已经取消 profile 这个概念了」）。
 * 判据三条：没赋值就是默认；赋了就是那一份、读回来还是它；未知值归回默认。
 * 最后一条是回归的指纹——旧的按 profile 分格那两张表要是被谁塞回来，这条就红。
 */

interface Pref {
  assign: (value: string) => void
  resolve: () => string
}

const cases = [
  { a: 'ember', fallback: DEFAULT_SKIN_NAME, junk: 'nope', name: 'skin', pref: skinPref as unknown as Pref },
  { a: 'dark', fallback: 'light', junk: 'dusk', name: 'mode', pref: modePref as unknown as Pref }
]

describe.each(cases)('全局一份的 $name', ({ a, fallback, junk, pref }) => {
  beforeEach(() => window.localStorage.clear())

  it('没赋值就是默认，赋了值读回来还是那一份', () => {
    expect(pref.resolve()).toBe(fallback)

    pref.assign(a)

    expect(pref.resolve()).toBe(a)
  })

  it('未知值归回默认', () => {
    pref.assign(junk)

    expect(pref.resolve()).toBe(fallback)
  })

  it('不再往按 profile 分格的那两张旧表里写东西', () => {
    pref.assign(a)

    expect(window.localStorage.getItem('plobi-desktop-profile-themes-v1')).toBeNull()
    expect(window.localStorage.getItem('plobi-desktop-profile-modes-v1')).toBeNull()
  })
})
