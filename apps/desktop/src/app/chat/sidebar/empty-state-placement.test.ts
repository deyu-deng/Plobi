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

  it('基准是中间那一栏（Agents 栏）的中点，不是它下面那片空白', async () => {
    const source = await readFile(SIDEBAR, 'utf8')

    // 中间那一栏：只有它滚（导航那几行钉在顶上），Agents 名单与空态都在它里面。
    const columnAt = source.indexOf('<div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">')
    expect(columnAt, '找不到中间那一栏').toBeGreaterThan(-1)

    // 占满那一栏的那一层（`min-h-full`）必须**直接开在那一栏里面**——中间不隔任何
    // `</div>`，否则它占满的就不是那一栏，标记的「中点」会偏。
    const stretchAt = source.indexOf('<div className="flex min-h-full flex-col">')
    expect(stretchAt, '找不到占满中段的那一层').toBeGreaterThan(columnAt)
    expect(source.slice(columnAt, stretchAt), '占满的那一层不是中间那一栏的直接子节点').not.toContain('</div>')
  })

  it('错误行与空态互斥（否则标记会被挤到那一栏中点之下）', async () => {
    const source = await readFile(SIDEBAR, 'utf8')

    // 0.3.0 的原意是「错误行」与「空态」二选一（`?:` 链）；错误行如果与空态并排，
    // 上头那行提示就把标记的落位顶偏了——而基准是那一栏的中点，不许被顶偏。
    expect(source, '错误行又与空态并排了').not.toContain('{secretaryAgentsError && (')
    expect(source, '错误行不在那条三元链上').toContain('secretaryAgentsError ? (')
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
