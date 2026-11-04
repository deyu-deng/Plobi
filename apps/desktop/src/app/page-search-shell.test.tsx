import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { PageSearchShell } from './page-search-shell'

/**
 * 页面壳头顶那条「回对话」的路（用户 11-03：「打开左侧边栏上部分的组件，有些没有返回
 * 操作，退回原聊天界面太麻烦了」）。判据两条：
 * ① 有分页没搜索、有搜索没分页、两样都没有——三种都得有那枚返回键；
 * ② 它在这条横排的最左边（搜索之前），不是挤在右上角跟别的动作抢位置。
 */

afterEach(cleanup)

function mount(props: Partial<Parameters<typeof PageSearchShell>[0]> = {}) {
  return render(
    <MemoryRouter>
      <PageSearchShell onSearchChange={() => {}} searchPlaceholder="搜索" searchValue="" {...props}>
        <div />
      </PageSearchShell>
    </MemoryRouter>
  )
}

describe('页面壳的返回键', () => {
  it('既没搜索也没分页时，头顶那一行仍然在（返回键不许跟着一起消失）', () => {
    mount({ searchHidden: true })

    expect(screen.getByRole('button', { name: 'Back' })).toBeTruthy()
    expect(screen.queryByPlaceholderText('搜索')).toBeNull()
  })

  it('有搜索时，返回键排在搜索左边', () => {
    mount()
    const back = screen.getByRole('button', { name: 'Back' })
    const search = screen.getByPlaceholderText('搜索')

    expect(back.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(back.innerHTML).toContain('codicon-arrow-left')
  })
})
