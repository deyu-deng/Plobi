import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Pane, PaneMain, PaneShell } from '@/components/pane-shell/pane-shell'
import { $paneStates } from '@/store/panes'

/**
 * 「只显示这一张卡」的动效靠两件事成立：
 *
 * ① **列数不变** —— `grid-template-columns` 是内联写在网格容器上的，列**个数**一变
 *    浏览器就无从插值，只能硬跳；列数不变时，那条 px 轨道可以从 320px 过渡到 0px。
 *    ⇒ 焦点态里右栏那两条轨**不许卸载**，只被收成 0 宽（`Pane` 的 `disabled`）。
 * ② **有过渡** —— 只在焦点换掉的那 240ms 里给网格挂 `transition`（`store/layout.ts`
 *    的 `$cardFocusAnimating` ＋ `app-shell.tsx`）。
 *
 * 用户 11-10：「某张卡片点击最大化的时候，没有相应的动效……我无法接受。」
 */

/** 桌面端 `src` 的位置（vitest 跑起来时 `import.meta.url` 是 dev server 的地址）。 */
function findSrcDir(): string {
  let dir = process.cwd()

  for (let depth = 0; depth < 6; depth += 1) {
    for (const candidate of [join(dir, 'src'), join(dir, 'apps', 'desktop', 'src')]) {
      if (existsSync(join(candidate, 'app'))) {
        return candidate
      }
    }

    const parent = dirname(dir)

    if (parent === dir) {
      break
    }

    dir = parent
  }

  throw new Error('找不到桌面端 src 目录')
}

function columnTemplate(container: HTMLElement): string[] {
  return (container.style.gridTemplateColumns ?? '').split(/\s+/).filter(Boolean)
}

describe('焦点态的列数', () => {
  beforeEach(() => {
    $paneStates.set({})
  })

  afterEach(() => {
    cleanup()
    $paneStates.set({})
  })

  it('右栏那条轨被收成 0 宽时，列数不变（列数一变，列宽就无从插值 ⇒ 硬跳）', () => {
    const rendered = render(
      <PaneShell>
        <PaneMain>chat</PaneMain>
        <Pane id="rail" side="right" width="320px">
          rail
        </Pane>
      </PaneShell>
    )

    const before = columnTemplate(rendered.container.firstElementChild as HTMLElement)

    expect(before).toHaveLength(2)
    expect(before[1]).toBe('320px')

    rendered.rerender(
      <PaneShell>
        <PaneMain>chat</PaneMain>
        {/* 焦点态：这条轨不卸载，只被收成 0 宽 */}
        <Pane disabled id="rail" side="right" width="320px">
          rail
        </Pane>
      </PaneShell>
    )
    const after = columnTemplate(rendered.container.firstElementChild as HTMLElement)

    expect(after).toHaveLength(before.length)
    expect(after[1]).toBe('0px')
    // 主区那条仍是弹性轨道 ⇒ 两边都是「px → px」，浏览器可以插值。
    expect(after[0]).toBe(before[0])
  })
})

describe('控制器的焦点接线', () => {
  it('焦点态那两条轨照旧渲染（不许被卸载）', () => {
    const source = readFileSync(join(findSrcDir(), 'app', 'desktop-controller.tsx'), 'utf8')

    // 焦点分支都带着那两条轨（`railPanes`）——列数不变是动效成立的前提。
    const flat = source.replace(/\s+/g, ' ')

    expect(source).toContain('const railPanes = [')
    expect(flat).toContain('focusChat ? [<PaneMain key="main">{chatCard}</PaneMain>, ...railPanes]')
    expect(flat).toContain('focusWorkbench ? [<PaneMain key="main">{railCard}</PaneMain>, ...railPanes]')
  })

  it('那两条轨在焦点态被强制收成 0 宽（`disabled` 带上焦点旗标）', () => {
    const source = readFileSync(join(findSrcDir(), 'app', 'desktop-controller.tsx'), 'utf8')

    expect(source).toContain('const railFocused = focusChat || focusWorkbench')
    expect(source).toContain('disabled={!isL2 || railFocused}')
    expect(source).toContain('disabled={level !== \'l1\' || railFocused}')
  })

  it('顶栏那 34px 的让位只从抽出来的纯函数走（控制器里不许再写死那串）', () => {
    const source = readFileSync(join(findSrcDir(), 'app', 'desktop-controller.tsx'), 'utf8')

    expect(source).not.toContain('pt-(--titlebar-height)')
    expect(source).toContain('resolveWorkbenchAsideClass({ panesFlipped, shellCards, swapped })')
  })
})
