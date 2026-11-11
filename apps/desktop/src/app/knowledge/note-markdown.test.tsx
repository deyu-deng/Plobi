// @vitest-environment jsdom
import { render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { makeLinkIndex, NoteMarkdown } from './note-markdown'

/**
 * wikilink 必须**真渲染**出来才算数——mock 测不出 remark 插件有没有生效、
 * 组件映射对不对。这里用真 DOM 核三件事：
 * ① `[[CATIA]]` 变成按钮，不是普通文字
 * ② 代码围栏里的 `[[…]]` 不被当成链接（AST 层面天然成立）
 * ③ 库里没有的目标被标成「未创建」，点不动
 */
const NOTES = [
  { path: 'Notes/工具/Modeling/Creo.md', title: 'Creo Parametric' },
  { path: 'Notes/数学/微积分.md', title: '微积分' }
]

describe('NoteMarkdown wikilink', () => {
  it('renders a cross-file wikilink as a button', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="看 [[Creo]] 这个" />)
    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    const button = container.querySelector('button')
    expect(button?.textContent).toBe('Creo')
  })

  it('renders an alias', async () => {
    const { container } = render(
      <NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="[[微积分|微积分笔记]]" />
    )

    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    expect(container.querySelector('button')?.textContent).toBe('微积分笔记')
  })

  it('marks a link to a note that does not exist as unresolvable', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="[[CATIA]]" />)
    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    const button = container.querySelector('button') as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('does not turn wikilinks inside fenced code into links', async () => {
    const text = ['```md', '![[image.png|300]]', '```'].join('\n')
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text={text} />)
    await waitFor(() => expect(container.textContent).toContain('image.png'))
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders an embed as a quiet badge, not a link', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="![[image.png|300]]" />)
    await waitFor(() => expect(container.textContent).toContain('image.png'))
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders a same-file anchor link as a button', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="[[#ch1|1. 发展历程]]" />)
    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    expect(container.querySelector('button')?.textContent).toBe('1. 发展历程')
  })
})

/**
 * 渲染层（WP-NOTES-RENDER）：用户 11-10 22:4x 问的「公式和 mermaid 和图片支持吗」，
 * 这一族把四件事钉死：
 * ① 公式走 KaTeX（与聊天、文件预览同一份实现）
 * ② GFM 没被顺带关掉 ⇒ 表格是表格、任务列表是复选框
 * ③ 本地图按「笔记同目录 → attachments/ → 库根」从盘上读出来
 * ④ 读不到就照实说，不装成图
 */
describe('NoteMarkdown 渲染层', () => {
  let restoreBridge: (() => void) | null = null

  /**
   * 把桌面那座桥塞进 window —— **只换 `plobiDesktop` 那一格**。
   *
   * 不能整个换掉 `window`：`{...window}` 的展开拿不到 jsdom 的 document，
   * 渲染结果直接变成 undefined（第一版就是这么撞的）。
   */
  function stubDesktopBridge(readFileDataUrl: (filePath: string) => Promise<string>) {
    const holder = window as unknown as { plobiDesktop?: unknown }
    const original = holder.plobiDesktop

    holder.plobiDesktop = { readFileDataUrl }

    restoreBridge = () => {
      holder.plobiDesktop = original
    }
  }

  afterEach(() => {
    restoreBridge?.()
    restoreBridge = null
    vi.restoreAllMocks()
  })

  it('把 $…$ 排成 KaTeX（正文里不再留裸 $）', async () => {
    const { container } = render(<NoteMarkdown text="动能 $E=\\frac{1}{2}mv^2$ 这样" />)

    await waitFor(() => expect(container.querySelector('.katex')).not.toBeNull())
    expect(container.textContent ?? '').not.toContain('$')
  })

  it('Markdown 表格是表格（GFM 没被顺带关掉）', async () => {
    const text = ['| 量 | 值 |', '| - | - |', '| v | 20 |'].join('\n')
    const { container } = render(<NoteMarkdown text={text} />)

    await waitFor(() => expect(container.querySelector('table')).not.toBeNull())
    expect(container.querySelectorAll('td').length).toBe(2)
  })

  it('任务列表是复选框', async () => {
    const { container } = render(<NoteMarkdown text={'- [x] 已读\n- [ ] 未读'} />)

    await waitFor(() => expect(container.querySelector('input[type="checkbox"]')).not.toBeNull())
  })

  it('本地图按笔记同目录从盘上读出来', async () => {
    const readFileDataUrl = vi.fn(async () => 'data:image/png;base64,AAAA')

    stubDesktopBridge(readFileDataUrl)

    const { container } = render(
      <NoteMarkdown assetRoot="/vault/notes" baseDir="/vault/notes/物理/大学物理" text="![插图](图.png)" />
    )

    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    expect(container.querySelector('img')?.getAttribute('src')).toBe('data:image/png;base64,AAAA')
    expect(readFileDataUrl).toHaveBeenCalledWith('/vault/notes/物理/大学物理/图.png')
  })

  it('Obsidian 的 `![[图.png]]` 也出真图', async () => {
    const readFileDataUrl = vi.fn(async () => 'data:image/png;base64,BBBB')

    stubDesktopBridge(readFileDataUrl)

    const { container } = render(
      <NoteMarkdown assetRoot="/vault/notes" baseDir="/vault/notes/物理/大学物理" text="![[图2.png|300]]" />
    )

    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    expect(container.querySelector('img')?.getAttribute('src')).toBe('data:image/png;base64,BBBB')
  })

  it('读不到的本地图落回带文件名的占位，不装成图', async () => {
    const readFileDataUrl = vi.fn(async () => {
      throw new Error('ENOENT')
    })

    stubDesktopBridge(readFileDataUrl)

    const { container } = render(
      <NoteMarkdown assetRoot="/vault/notes" baseDir="/vault/notes/物理/大学物理" text="![插图](缺.png)" />
    )

    // 占位上写着作者给的说明，路径挂在 title 上——「哪一张图没找到」要能一眼看见。
    await waitFor(() => expect(container.textContent).toContain('插图'))
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[title="缺.png"]')).not.toBeNull()
  })

  it('外链图片照旧交给浏览器', async () => {
    const { container } = render(<NoteMarkdown text="![插图](https://example.com/a.png)" />)

    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    expect(container.querySelector('img')?.getAttribute('src')).toBe('https://example.com/a.png')
  })
})
