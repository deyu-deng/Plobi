import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { ChatSwapOverlay } from './chat-swap-overlay'

/**
 * 「对话卡的界面一直在闪」那一条的判据。
 *
 * 用户 11-12：装机版 0.3.1 的对话卡片界面持续闪烁（渲染进程吃着一个多核、
 * 常驻 8 GB 并且还在涨）。排查时在开发态实例上量到：这一层过渡**只要不是
 * failed 就挂着那枚粒子 Loader**——哪怕它自己 `opacity-0`（看不见）也一样挂。
 * 于是**每一张对话卡**都常驻一条 `requestAnimationFrame` 循环：每帧重算一条
 * 220 步的 SVG 路径，并把 40 个粒子的 cx/cy/r/opacity 逐个改一遍——实测约
 * 1.7 万次 DOM 改写/秒（摘掉这一层后 3 秒内的改写数从 53,232 掉到 480）。
 * 看不见的东西不该还在动。
 *
 * 判据：不在等的时候，这一层**不许**有粒子动画在跑。
 */
describe('对话卡过渡层的粒子动画', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(performance.now()), 0)
    )
    vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))
  })

  it('不在等的时候不挂粒子 Loader（否则每张对话卡都常驻一条 rAF 循环）', () => {
    const { container } = render(
      <I18nProvider configClient={null}>
        <ChatSwapOverlay profile={null} />
      </I18nProvider>
    )

    expect(container.querySelector('svg circle')).toBeNull()
  })

  it('真的在换 Agent（profile 有值）时才挂粒子 Loader', () => {
    const { container } = render(
      <I18nProvider configClient={null}>
        <ChatSwapOverlay profile="plobi" />
      </I18nProvider>
    )

    expect(container.querySelector('svg circle')).not.toBeNull()
  })
})
