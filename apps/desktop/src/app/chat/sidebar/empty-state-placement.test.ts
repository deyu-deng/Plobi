import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * 侧栏空态那枚产品标记的**落位**（用户 11-08 装上 0.3.0 后的现场反馈：
 * 「左边那个小乌龟的图标上下没有居中。有点奇怪。」）。
 *
 * 它原来贴着「Agents」那几行、下面空一大片，所以看着像悬在半空——不是图形画歪了，
 * 是**位置**没居中（用户：「没要你改小乌龟的形状呀…你调一下」）。
 *
 * 这条屏没有组件级测试档（侧栏根组件太重，起不来），所以按本仓已有的「读源码钉契约」
 * 那一路来（`app/console/architecture.guard.test.ts` 是同一个做法）：钉住标记所在那一层
 * 必须占满中段的可用空间并把它上下居中。
 */
const SIDEBAR = join(process.cwd(), 'src', 'app', 'chat', 'sidebar', 'index.tsx')

describe('侧栏空态标记的落位', () => {
  it('标记在可用空间里上下居中（不是贴着「Agents」那几行）', async () => {
    const source = await readFile(SIDEBAR, 'utf8')

    const wrapper = source.match(
      /<div className="([^"]*)">\s*<BrandMark className="size-12 bg-transparent opacity-30" \/>/
    )

    expect(wrapper, '侧栏空态那枚品牌标记不见了').not.toBeNull()
    expect(wrapper?.[1]).toContain('flex-1')
    expect(wrapper?.[1]).toContain('items-center')
    expect(wrapper?.[1]).toContain('justify-center')
  })

  it('空态不再直接塞在 SidebarMenu(<ul>) 里面', async () => {
    const source = await readFile(SIDEBAR, 'utf8')

    const mark = source.indexOf('<BrandMark className="size-12 bg-transparent opacity-30" />')
    expect(mark).toBeGreaterThan(-1)

    // 空态那一支现在挂在 `<div className="flex min-h-full flex-col">` 下：那一层占满中段的
    // 可用空间，标记才有「上下居中」可依。名单那一支仍在 SidebarMenu 里（那是列表）。
    expect(source).toContain('<div className="flex min-h-full flex-col">')

    // 标记之前最后一个 <SidebarMenu> 已经在标记之前闭合 ⇒ 标记不在任何一个 <ul> 里
    // （div 直接放在 ul 下是非法嵌套）。
    const menuOpen = source.lastIndexOf('<SidebarMenu', mark)
    const menuClose = source.lastIndexOf('</SidebarMenu>', mark)
    expect(menuOpen, '标记之前找不到 SidebarMenu').toBeLessThan(menuClose)
  })
})
