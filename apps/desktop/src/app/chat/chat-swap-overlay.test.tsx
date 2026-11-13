import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PLOBI_MARK_PATH } from '@/components/plobi-glyphs'
import { I18nProvider } from '@/i18n'

import { ChatSwapOverlay } from './chat-swap-overlay'

// vitest jsdom 下 `import.meta.url` 是 http 地址 ⇒ 按 cwd（桌面应用根）定位。
const read = (rel: string): string => readFileSync(join(process.cwd(), 'src', 'app', 'chat', rel), 'utf8')

/**
 * 「对话卡的界面一直在闪」＋「加载画面单独显示」那两条的判据。
 *
 * 用户 11-12：装机版 0.3.1 的对话卡片界面持续闪烁（渲染进程吃着一个多核、
 * 常驻 8 GB 并且还在涨）。排查时在开发态实例上量到：这一层过渡**只要不是
 * failed 就挂着那枚粒子 Loader**——哪怕它自己 `opacity-0`（看不见）也一样挂。
 * 于是**每一张对话卡**都常驻一条 `requestAnimationFrame` 循环：每帧重算一条
 * 220 步的 SVG 路径，并把 40 个粒子的 cx/cy/r/opacity 逐个改一遍——实测约
 * 1.7 万次 DOM 改写/秒（摘掉这一层后 3 秒内的改写数从 53,232 掉到 480）。
 * 看不见的东西不该还在动。
 *
 * 用户 11-13 12:35：加载动画换成品牌小乌龟沿进度条左→右；而且切换界面时
 * **加载画面单独显示**，不叠在切换前的聊天正文上（「重叠在字上面很丑」）。
 *
 * 判据：不在等的时候，这一层**不许**有加载指示在跑；在等的时候，底下那层
 * 必须是**不透明**的（旧正文一点都透不出来）。
 */
describe('对话卡过渡层', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(performance.now()), 0)
    )
    vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))
  })

  it('不在等的时候不挂加载指示（否则每张对话卡都常驻一条动画循环）', () => {
    const { container } = render(
      <I18nProvider configClient={null}>
        <ChatSwapOverlay profile={null} />
      </I18nProvider>
    )

    expect(container.querySelector('svg')).toBeNull()
    expect(container.querySelector('[data-testid="chat-swap-overlay-backdrop"]')).toBeNull()
  })

  it('真的在换 Agent（profile 有值）时才挂品牌龟', () => {
    const { container } = render(
      <I18nProvider configClient={null}>
        <ChatSwapOverlay profile="plobi" />
      </I18nProvider>
    )

    expect(container.querySelector('svg path')?.getAttribute('d')).toBe(PLOBI_MARK_PATH)
  })

  it('加载画面单独显示：底下那层是不透明的（旧聊天正文不透出来）', () => {
    const { container } = render(
      <I18nProvider configClient={null}>
        <ChatSwapOverlay profile="plobi" />
      </I18nProvider>
    )

    const backdrop = container.querySelector('[data-testid="chat-swap-overlay-backdrop"]')

    expect(backdrop).not.toBeNull()
    expect(backdrop?.className).toContain('bg-(--ui-chat-surface-background)')
  })

  it('过渡层挂在 composer 所在的那一层之外（否则盖不住输入框，旧正文还会透出来）', () => {
    // composer（`ChatBar`）在 `ChatRuntimeBoundary` 里面、转录容器**外面**；
    // 过渡层挂在转录容器里就只盖得住转录 ⇒ 「加载画面单独显示」是假的。
    const source = read('index.tsx')
    const boundaryEnd = source.indexOf('</ChatRuntimeBoundary>')
    const overlay = source.indexOf('<ChatSwapOverlay')

    expect(boundaryEnd, '读不到 `</ChatRuntimeBoundary>`（结构变了，先看一眼这一条）').toBeGreaterThan(-1)
    expect(overlay, '读不到 `<ChatSwapOverlay>`（结构变了，先看一眼这一条）').toBeGreaterThan(-1)
    expect(
      overlay,
      '过渡层又挂回转录容器里了 ⇒ 切换时盖不住输入框，旧聊天正文会从底下透出来'
    ).toBeGreaterThan(boundaryEnd)
  })
})
