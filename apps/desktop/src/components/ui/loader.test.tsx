import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PLOBI_MARK_PATH } from '@/components/plobi-glyphs'

import { Loader } from './loader'

/**
 * 「加载态统一成品牌小乌龟沿进度条左→右」那一条的判据（用户 11-13 12:35；
 * 11-17 08:2x 追加三条：不往返跑、颜色跟主题、屏上不出现文字）。
 *
 * 四条契约钉在这一档组件上：
 *
 * 1. **形状只有一只龟** —— 走的是品牌字形 `PLOBI_MARK_PATH`，不新画第二只。
 * 2. **走位是纯 CSS** —— 组件里**不许**有 `requestAnimationFrame`／`setAttribute`。
 *    上一刀 `WP-CHAT-CARD-FLICKER` 的病根正是那枚粒子 `Loader`：它每帧重算一条
 *    220 步的 SVG 路径、并把 40 个粒子的 `cx/cy/r/opacity` 逐个改写（实测约
 *    1.7 万次 DOM 改写/秒，窗口最小化时照改 ⇒ 屏上「一直在闪」）。这一条是
 *    **防复发**的：形状再好看，逐帧改 DOM 的那套东西不许回来。
 * 3. **不往返跑**（11-17 主人拍：太简陋）—— 位移只有「左 → 右」一段；到右端淡出，
 *    循环时从左边重新进场。**不许**再出现「走到头翻面、再走回来」那套。
 * 4. **颜色跟用户选的主题走** —— `--ui-accent`（＝ 运行时注入的 `--theme-midground`）。
 *
 * 外加一条老契约：**系统说「别动」就别动** —— `prefers-reduced-motion: reduce` 时
 * 动画停掉（与 `app/chat/thread-swap.ts` 同一口径：不是改成「更短的动效」）。
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

/** 取一个 `@keyframes` 名字对应的整段关键帧（要数花括号：每档停止点自己带一层）。 */
const keyframes = (css: string, name: string): string => {
  const start = css.indexOf(`@keyframes ${name}`)

  if (start < 0) {
    return ''
  }

  const open = css.indexOf('{', start)

  let depth = 0

  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') {
      depth += 1
    } else if (css[i] === '}') {
      depth -= 1

      if (depth === 0) {
        return css.slice(open + 1, i)
      }
    }
  }

  return ''
}

describe('小乌龟加载态', () => {
  it('画的是品牌龟那一条路径（不新画第二只龟）', () => {
    const { container } = render(<Loader />)

    expect(container.querySelector('svg path')?.getAttribute('d')).toBe(PLOBI_MARK_PATH)
  })

  it('走位是 CSS：跑道／位移层／尾迹／龟四层，组件里没有逐帧改 DOM 的那套东西', () => {
    const { container } = render(<Loader />)

    expect(container.querySelector('.loader__track')).not.toBeNull()
    expect(container.querySelector('.loader__rail')).not.toBeNull()
    expect(container.querySelector('.loader__runner')).not.toBeNull()
    expect(container.querySelector('.loader__trail')).not.toBeNull()
    expect(container.querySelector('.loader__turtle')).not.toBeNull()
    // 旧的花加载器是几十个 <circle> 粒子；这一档一个都没有。
    expect(container.querySelector('svg circle')).toBeNull()

    const source = stripComments(read('loader.tsx'))

    expect(source, '加载态不许再逐帧改 DOM——那是上一刀那场「一直在闪」的病根').not.toMatch(
      /requestAnimationFrame|setAttribute/
    )
  })

  it('不往返跑：位移只有左→右一段，换位那一瞬是透明的', () => {
    const css = read('loader.css')
    const run = keyframes(css, 'plobi-turtle-run')

    expect(run, '跑位关键帧不见了').not.toBe('')
    // 行程 = 跑道宽 − 龟宽 ⇒ 龟正好停在右端，不会有一半挂到跑道外面。
    expect(run).toMatch(/translateX\(calc\(100% - var\(--loader-size\)\)\)/)
    // 全程只许出现一次 translateX(0)：出现第二次就是「走回去」了。
    expect(run.match(/translateX\(0\)/g)?.length ?? 0).toBe(1)
    // 上一版那个「走到头翻面」的折返关键帧不许回来。
    expect(css).not.toMatch(/@keyframes plobi-turtle-turn/)
    // 循环接缝（小龟瞬移回左端）要藏在淡出／淡入里 ⇒ 这一段必须有 0 与 1 两种不透明度。
    const fade = keyframes(css, 'plobi-turtle-fade')

    expect(fade).toMatch(/opacity:\s*0/)
    expect(fade).toMatch(/opacity:\s*1/)
  })

  it('颜色跟用户选的主题走（--ui-accent），不写死成文字色', () => {
    const css = read('loader.css')

    expect(css).toMatch(/color:\s*var\(--ui-accent\)/)
    // 组件自己不再挂 text-primary（那是跟周围文字同色，发闷）。
    expect(stripComments(read('loader.tsx'))).not.toMatch(/text-primary/)
  })

  it('走位写在 CSS 里，系统说「别动」时真的不动', () => {
    const css = read('loader.css')

    expect(css).toMatch(/@keyframes plobi-turtle-run/)
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/)
  })

  it('脚要真的踩在跑道上：字标按实际外框裁，盒高按高宽比算', () => {
    const { container } = render(<Loader />)
    const svg = container.querySelector('svg.loader__turtle')

    // 品牌龟在 64 网格里只占 x=4–58、y=20–47.5 ⇒ 取景框必须贴紧，
    // 否则龟会**悬在跑道上方 4.6px**（比龟身一半还高，像浮着跑）。
    expect(svg?.getAttribute('viewBox')).toBe('4 20 54 27.5')

    const css = read('loader.css')

    expect(css).toMatch(/--loader-ratio:\s*27\.5 \/ 54/)
    expect(css).toMatch(/height:\s*calc\(var\(--loader-size\) \* var\(--loader-ratio\)\)/)
  })

  it('尺寸档换的是龟身与跑道的大小', () => {
    const { container } = render(<Loader size="lg" />)
    const root = container.querySelector('.loader') as HTMLElement

    expect(root.style.getPropertyValue('--loader-size')).toBe('24px')
    expect(root.style.getPropertyValue('--loader-width')).toBe('12rem')
  })

  it('紧凑档不画跑道（格子只有十几二十像素，走不动）', () => {
    const { container } = render(<Loader size="dot" />)
    const root = container.querySelector('.loader')

    expect(root?.classList.contains('loader--dot')).toBe(true)
  })
})
