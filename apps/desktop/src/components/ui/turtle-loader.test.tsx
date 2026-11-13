import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PLOBI_MARK_PATH } from '@/components/plobi-glyphs'

import { TurtleLoader } from './turtle-loader'

/**
 * 「加载态统一成品牌小乌龟沿进度条左→右」那一条的判据（用户 11-13 12:35）。
 *
 * 三条契约钉在这一档组件上：
 *
 * 1. **形状只有一只龟** —— 走的是品牌字形 `PLOBI_MARK_PATH`，不新画第二只。
 * 2. **走位是纯 CSS** —— 组件里**不许**有 `requestAnimationFrame`／`setAttribute`。
 *    上一刀 `WP-CHAT-CARD-FLICKER` 的病根正是那枚粒子 `Loader`：它每帧重算一条
 *    220 步的 SVG 路径、并把 40 个粒子的 `cx/cy/r/opacity` 逐个改写（实测约
 *    1.7 万次 DOM 改写/秒，窗口最小化时照改 ⇒ 屏上「一直在闪」）。这一条是
 *    **防复发**的：形状再好看，逐帧改 DOM 的那套东西不许回来。
 * 3. **系统说「别动」就别动** —— `prefers-reduced-motion: reduce` 时动画停掉
 *    （与 `app/chat/thread-swap.ts` 同一口径：不是改成「更短的动效」）。
 */

// vitest jsdom 下 `import.meta.url` 是 http 地址 ⇒ 按 cwd（桌面应用根）定位。
const read = (rel: string): string => readFileSync(join(process.cwd(), 'src', 'components', 'ui', rel), 'utf8')

/**
 * 判据只扫**代码**，不扫注释。
 *
 * 这份组件的注释里必须能写清「为什么不用 rAF」——那句话里本来就有
 * `requestAnimationFrame` 这个词；连注释一起扫，判据会自己命中自己，
 * 然后逼着人把注释改得说不清楚。剥掉注释再判，判的才是真的那件事。
 */
const stripComments = (source: string): string =>
  source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('小乌龟加载态', () => {
  it('画的是品牌龟那一条路径（不新画第二只龟）', () => {
    const { container } = render(<TurtleLoader />)

    expect(container.querySelector('svg path')?.getAttribute('d')).toBe(PLOBI_MARK_PATH)
  })

  it('走位是 CSS：跑道／位移层／龟三层，组件里没有逐帧改 DOM 的那套东西', () => {
    const { container } = render(<TurtleLoader />)

    expect(container.querySelector('.turtle-loader__track')).not.toBeNull()
    expect(container.querySelector('.turtle-loader__rail')).not.toBeNull()
    expect(container.querySelector('.turtle-loader__runner')).not.toBeNull()
    expect(container.querySelector('.turtle-loader__turtle')).not.toBeNull()
    // 旧的花加载器是几十个 <circle> 粒子；这一档一个都没有。
    expect(container.querySelector('svg circle')).toBeNull()

    const source = stripComments(read('turtle-loader.tsx'))

    expect(source, '加载态不许再逐帧改 DOM——那是上一刀那场「一直在闪」的病根').not.toMatch(
      /requestAnimationFrame|setAttribute/
    )
  })

  it('走位与翻面写在 CSS 里，系统说「别动」时真的不动', () => {
    const css = read('turtle-loader.css')

    expect(css).toMatch(/@keyframes plobi-turtle-walk/)
    // 行程 = 跑道宽 − 龟宽 ⇒ 龟正好停在右端，不会有一半挂到跑道外面。
    expect(css).toMatch(/translateX\(calc\(100% - var\(--turtle-size\)\)\)/)
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/)
  })

  it('尺寸档换的是龟身与跑道的大小', () => {
    const { container } = render(<TurtleLoader size="lg" />)
    const root = container.querySelector('.turtle-loader') as HTMLElement

    expect(root.style.getPropertyValue('--turtle-size')).toBe('24px')
    expect(root.style.getPropertyValue('--turtle-width')).toBe('12rem')
  })

  it('紧凑档不画跑道（格子只有十几二十像素，走不动）', () => {
    const { container } = render(<TurtleLoader size="dot" />)
    const root = container.querySelector('.turtle-loader')

    expect(root?.classList.contains('turtle-loader--dot')).toBe(true)
  })
})
