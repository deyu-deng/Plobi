import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { MarkdownPreview } from './file-preview'

// 用户 11-03 在 `大物小测-公式卡.md` 的预览里看到一整页裸 LaTeX（`$\mathbf{F}=\dfrac…`）。
// 成因是文件预览的 Streamdown 没挂 math 插件——聊天那边挂了，预览这边漏了。
// 这条用例盯的就是「预览与聊天同一个渲染能力」：`$…$` 必须变成 KaTeX，而不是当文字输出。
describe('MarkdownPreview math', () => {
  afterEach(cleanup)

  it('renders inline $…$ through KaTeX instead of printing the source', () => {
    const { container } = render(
      <MarkdownPreview text={'场强定义：$\\mathbf{E}=\\dfrac{1}{4\\pi\\varepsilon_0}\\dfrac{q}{r^2}$'} />
    )

    expect(container.querySelector('.katex')).not.toBeNull()
    // 定界符必须被吃掉。KaTeX 的 MathML 注解里会留一份原始 TeX（屏幕阅读器用），
    // 那是不可见的，所以只断言 `$` 不再出现在正文里。
    expect(container.textContent ?? '').not.toContain('$')
  })

  it('leaves dollar-free markdown alone', () => {
    const { container } = render(<MarkdownPreview text={'静电场一节：库仑力与场强定义'} />)

    expect(container.querySelector('.katex')).toBeNull()
    expect(container.textContent ?? '').toContain('库仑力')
  })
})
