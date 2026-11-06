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

  it('嵌在别的屏里时不画返回键（传 showBack=false）', () => {
    // Capabilities 那一屏嵌在设置里：那枚返回键会把人送回对话，把宿主整张撤掉
    // （用户 11-05 的截图里正是这个形状）。
    mount({ showBack: false })

    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
  })
})

describe('页面壳的表头（这一屏属于哪个 Agent）', () => {
  // 用户 11-05：每个 Agent 主界面侧边栏点进来的能力页，头顶要有一行表头，**只写
  // Agent 名**（不许加「专属」之类的修饰）；设置里那一份是全局的，什么都不写。
  it('传 pageTitle 时就写这一个名字，一个字不多', () => {
    mount({ pageTitle: '总秘书' })

    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('总秘书')
  })

  it('不传 pageTitle 时头顶没有表头（全局那一页不加任何标识）', () => {
    mount()

    expect(screen.queryByRole('heading', { level: 2 })).toBeNull()
  })
})
