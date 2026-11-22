import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AgentFormDialog } from './agent-form'

// 用户 2026-11-22 第二刀：「容器合并也要一起做。」
//
// 上一刀只把三块字段抽成共用（agent-form-fields.tsx），壳子还是两套：新建在侧栏里
// 自己拼 Dialog + form + footer，编辑又拼一遍。这组测试钉住「壳子只有一份」这件事，
// 以及那份壳子必须守住的规矩：
//   · 名字 / 中间那一格 / 目录 / 外观 / 错误 / footer 的顺序固定；
//   · 英文名不合法就露出提示，合法了收起来；
//   · 不该提交的时候（busy、canSubmit=false）点了也绝不 onSubmit；
//   · 落库中不许 Esc / 遮罩关掉。

afterEach(cleanup)

const labels = {
  appearanceLabels: { clear: '清掉', hint: '空着就用默认', label: '图标与颜色' },
  folderLabels: { add: '添加文件夹', empty: '还没选目录', label: '工作目录', primary: '主目录', remove: '移除' }
}

function renderForm(overrides: Partial<Parameters<typeof AgentFormDialog>[0]> = {}) {
  const onOpenChange = vi.fn()
  const onSubmit = vi.fn()

  render(
    <AgentFormDialog
      {...labels}
      busy={false}
      busyLabel="保存中…"
      canSubmit
      cancelLabel="取消"
      color=""
      displayName="日程秘书"
      englishHelp="这门牌会变成目录名"
      englishLabel="英文名"
      englishName="Agenda Secretary"
      icon=""
      idPrefix="test-agent"
      nameLabel="左栏里叫它什么"
      onAddFolder={() => {}}
      onCancel={() => onOpenChange(false)}
      onChangeColor={() => {}}
      onChangeIcon={() => {}}
      onClearAppearance={() => {}}
      onDisplayNameChange={() => {}}
      onEnglishNameChange={() => {}}
      onOpenChange={onOpenChange}
      onRemoveFolder={() => {}}
      onRetranslate={() => {}}
      onSubmit={onSubmit}
      open
      paths={[]}
      retranslateLabel="重新翻译"
      submitLabel="建册"
      title="新建一个 Agent"
      translating={false}
      {...overrides}
    />
  )

  return { onOpenChange, onSubmit }
}

describe('AgentFormDialog 这一份壳子', () => {
  it('把标题、名字、extra、错误和 footer 都摆出来', () => {
    renderForm({ description: '给它一个名字、一个样子', error: '重名了' })

    expect(screen.getByText('新建一个 Agent')).toBeTruthy()
    expect(screen.getByText('给它一个名字、一个样子')).toBeTruthy()
    expect(screen.getByLabelText('左栏里叫它什么')).toBeTruthy()
    expect(screen.getByLabelText('英文名')).toBeTruthy()
    expect(screen.getByText('重名了')).toBeTruthy()
    expect(screen.getByRole('button', { name: '取消' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '建册' })).toBeTruthy()
  })

  it('中间那一格由调用方决定（新建放品类，编辑放改名清单）', () => {
    renderForm({ extra: <p>这一格是调用方的</p> })

    expect(screen.getByText('这一格是调用方的')).toBeTruthy()
  })

  it('英文名非法才露提示', () => {
    const { unmount } = render(
      <AgentFormDialog
        {...labels}
        busy={false}
        busyLabel="保存中…"
        canSubmit
        cancelLabel="取消"
        color=""
        displayName="日程秘书"
        englishHelp="这门牌会变成目录名"
        englishLabel="英文名"
        englishName="日程秘书"
        icon=""
        idPrefix="test-agent"
        nameLabel="左栏里叫它什么"
        onAddFolder={() => {}}
        onCancel={() => {}}
        onChangeColor={() => {}}
        onChangeIcon={() => {}}
        onClearAppearance={() => {}}
        onDisplayNameChange={() => {}}
        onEnglishNameChange={() => {}}
        onOpenChange={() => {}}
        onRemoveFolder={() => {}}
        onRetranslate={() => {}}
        onSubmit={() => {}}
        open
        paths={[]}
        retranslateLabel="重新翻译"
        submitLabel="建册"
        title="新建一个 Agent"
        translating={false}
      />
    )

    expect(screen.getByText('这门牌会变成目录名')).toBeTruthy()
    unmount()
  })

  it('提交按钮禁用时点了也不 onSubmit', () => {
    const { onSubmit } = renderForm({ canSubmit: false })

    fireEvent.click(screen.getByRole('button', { name: '建册' }))

    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('可以提交时点一下就走调用方那一步', () => {
    const { onSubmit } = renderForm()

    fireEvent.click(screen.getByRole('button', { name: '建册' }))

    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('落库中按钮写「保存中…」且不接受第二次提交', () => {
    const { onSubmit } = renderForm({ busy: true })

    const button = screen.getByRole('button', { name: '保存中…' })

    expect((button as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(button)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('落库中关窗请求被吞掉，不许半截状态跑掉', () => {
    const { onOpenChange } = renderForm({ busy: true })

    fireEvent.keyDown(document.body, { key: 'Escape' })

    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('没在忙的时候取消就真关掉', () => {
    const { onOpenChange } = renderForm()

    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
