// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MonitorEmpty, MonitorPanel, MonitorRow, MonitorSection, MonitorTopBarRow } from './monitor-panel'
import { TopBarIconButton } from './top-bar-icon-button'

/**
 * 统一容器（Qoder 任务监控面板的规格表）的判据。
 *
 * 钉的是**规格**不是源码文字：Qoder 好看不是「没有边框」，是所有零件同一把尺
 * —— 280px 列、28px 行、24px 方形图标托、12px 字、零描边、hover 才出底色。
 * 口径与实据见 `Docs/tasks/qoder-monitor-spec-task.md` §2。
 */

afterEach(() => {
  cleanup()
})

describe('统一容器 —— 两个形态', () => {
  it('固定列：280px 宽、占位兄弟列、**本体没有描边**（Qoder aside 本体无 border）', () => {
    const { container } = render(<MonitorPanel mode="column">内容</MonitorPanel>)
    const panel = container.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel.className).toContain('w-[280px]')
    expect(panel.className).toContain('shrink-0')
    // 描边：Qoder 那边靠字色与留白分层，浮层壳才有边框
    expect(panel.className).not.toMatch(/(^|\s)border(-[trblxy])?(\s|$)/)
    expect(panel.className).not.toContain('border-l')
  })

  it('浮层：280px 宽、圆角、淡边框、投影（Qoder `w-[280px] rounded-lg border-border-tertiary shadow-lg`）', () => {
    const { container } = render(<MonitorPanel mode="float">内容</MonitorPanel>)
    const panel = container.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel.className).toContain('w-[280px]')
    expect(panel.className).toContain('rounded-lg')
    expect(panel.className).toContain('shadow-lg')
    expect(panel.className).toMatch(/(^|\s)border(-[trblxy])?(\s|$)/)
    // 高度上限是**一个数**（Qoder `max-h-[560px]`）—— 这一族不许各写各的。
    expect(panel.className).toContain('max-h-[560px]')
    // 定位归使用方（贴着对话卡走还是钉在视口上，是用法不是长相）
    expect(panel.className).not.toContain('fixed')
  })

  it('形态写在 data 上（屏上与测试都认它，不认猜的类名）', () => {
    const { container } = render(<MonitorPanel mode="float">内容</MonitorPanel>)
    const panel = container.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel.getAttribute('data-monitor-mode')).toBe('float')
  })
})

describe('顶栏那一行（用户 11-19 22:0x：并排列不该占据顶栏）', () => {
  it('是头**上面**那一行：高 ＝ 顶栏那一行，接的是同一条底线', () => {
    const { container } = render(<MonitorTopBarRow />)
    const row = container.querySelector('[data-monitor-topbar-row]') as HTMLElement

    expect(row).toBeTruthy()
    expect(row.className).toContain('h-(--card-header-height)')
    expect(row.className).toContain('border-b')
    // 与 `ChatTopBar` 那条线同色（`--ui-stroke-tertiary`）⇒ 顶栏看着横跨整卡
    expect(row.className).toContain('border-(--ui-stroke-tertiary)')
    expect(row.className).toContain('bg-(--ui-chat-surface-background)')
  })

  it('并排列：那一行排在**头上面**（头在顶栏下一行）', () => {
    const { container } = render(
      <MonitorPanel header={<span>档案</span>} mode="column" topRow={<MonitorTopBarRow />}>
        内容
      </MonitorPanel>
    )

    const panel = container.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel.firstElementChild?.getAttribute('data-monitor-topbar-row')).toBe('')
    expect(panel.children[1].getAttribute('data-monitor-header')).toBe('')
  })
})

describe('卡头：头下没有那条线（Qoder 第二张那样：纯文字 ＋ 右上角图钉）', () => {
  it('头那个 div 上没有描边', () => {
    const { container } = render(
      <MonitorPanel header={<span>档案</span>} mode="column">
        内容
      </MonitorPanel>
    )

    const header = container.querySelector('[data-monitor-header]') as HTMLElement

    expect(header).toBeTruthy()
    expect(header.className).not.toMatch(/(^|\s)border(-[trblxy])?(\s|$)/)
  })
})

describe('分区（Qoder：13 个分区各能单独收起，标题就是一行 12px 字）', () => {
  it('折起时内容不渲染，标题还在', () => {
    const { rerender } = render(
      <MonitorSection collapsed={false} onToggle={() => {}} title="进度">
        <span>内容</span>
      </MonitorSection>
    )

    expect(screen.getByText('内容')).toBeTruthy()

    rerender(
      <MonitorSection collapsed onToggle={() => {}} title="进度">
        <span>内容</span>
      </MonitorSection>
    )
    expect(screen.queryByText('内容')).toBeNull()
    expect(screen.getByText('进度')).toBeTruthy()
  })

  it('折叠状态用一颗状态点表达（Qoder `size-1.5 rounded-full`），不是常驻折角', () => {
    const { container } = render(
      <MonitorSection collapsed onToggle={() => {}} title="进度">
        <span>内容</span>
      </MonitorSection>
    )

    const dot = container.querySelector('[data-monitor-collapsed-dot]') as HTMLElement
    expect(dot).toBeTruthy()
    expect(dot.className).toContain('size-1.5')
    expect(dot.className).toContain('rounded-full')
    // 标题行里不许有常驻的展开箭头
    expect(container.querySelector('svg')).toBeNull()
  })

  it('点标题才切换', () => {
    const onToggle = vi.fn()
    render(
      <MonitorSection onToggle={onToggle} title="进度">
        <span>内容</span>
      </MonitorSection>
    )

    fireEvent.click(screen.getByRole('button'))
    expect(onToggle).toHaveBeenCalled()
  })

  it('标题是 12px 一档、四级字色（Qoder `text-xs text-text-quaternary`）', () => {
    const { container } = render(<MonitorSection title="进度">内容</MonitorSection>)
    const title = screen.getByText('进度')

    expect(title.className).toContain('text-xs')
    expect(title.className).toContain('text-(--ui-text-quaternary)')
    expect(container.querySelector('[data-monitor-section]')).toBeTruthy()
  })
})

describe('一行（Qoder：28px 高、零描边、hover 才出底色）', () => {
  it('行高 28px、无描边、hover 底色、12px 字', () => {
    const { container } = render(<MonitorRow label="本地" value="aura" />)
    const row = container.querySelector('[data-monitor-row]') as HTMLElement

    expect(row.className).toContain('min-h-7')
    expect(row.className).toContain('rounded-md')
    // `border-0` 是「零描边」的正身；描边指的是 `border`／`border-t` 那一类
    expect(row.className).toContain('border-0')
    expect(row.className).not.toMatch(/(^|\s)border(-[trblxy])?(\s|$)/)
    expect(row.className).toContain('hover:bg-(--ui-row-hover-background)')
    expect(row.className).toContain('text-xs')
  })

  it('两栏：标签在左、值右对齐（Qoder label ＋ `ml-auto` 的 value）', () => {
    render(<MonitorRow label="本地" value="aura" />)

    const label = screen.getByText('本地')
    const value = screen.getByText('aura')

    expect(label.className).toContain('text-(--ui-text-secondary)')
    expect(value.className).toContain('ml-auto')
    expect(value.className).toContain('text-(--ui-text-tertiary)')
  })

  it('窄形态（固定列）：标签收进 tooltip，只剩图标 ＋ 值（Qoder `displayMode==="fixed"`）', () => {
    const { container } = render(<MonitorRow dense icon={<span>图</span>} label="本地" value="aura" />)

    const label = screen.getByText('本地')
    expect(label.className).toContain('sr-only')
    expect(label.getAttribute('title')).toBe('本地')
    expect(container.querySelector('[data-monitor-icon]')).toBeTruthy()
    expect(screen.getByText('aura')).toBeTruthy()
  })

  it('图标托是 24px 方形底染（Qoder `size-6 squircle-lg bg-fill-secondary`）', () => {
    const { container } = render(<MonitorRow icon={<span>图</span>} label="本地" />)
    const icon = container.querySelector('[data-monitor-icon]') as HTMLElement

    expect(icon.className).toContain('size-6')
    expect(icon.className).toContain('bg-(--ui-bg-secondary)')
  })

  it('可点的行是个按钮', () => {
    const onClick = vi.fn()
    render(<MonitorRow label="本地" onClick={onClick} />)

    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalled()
  })

  it('按下态与「动作在飞就别再点」都接得住（旧 rail 行的两条，不能丢）', () => {
    const onClick = vi.fn()

    const { rerender } = render(
      <MonitorRow ariaPressed label="写代码" onClick={onClick} />
    )

    const row = screen.getByRole('button')

    expect(row.getAttribute('aria-pressed')).toBe('true')

    rerender(<MonitorRow ariaPressed={false} disabled label="写代码" onClick={onClick} />)

    const disabledRow = screen.getByRole('button') as HTMLButtonElement

    expect(disabledRow.disabled).toBe(true)
    expect(disabledRow.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(disabledRow)
    expect(onClick).not.toHaveBeenCalled()
  })
})

describe('空态：一行破折号（屏上纪律：空的那块就空着）', () => {
  it('就是一根破折号', () => {
    render(<MonitorEmpty />)
    expect(screen.getByText('—')).toBeTruthy()
  })
})

describe('入口按钮：对话卡右上角那一族同一形状（用户 11-19 16:0x「三个按钮一人一个样」）', () => {
  it('28px 方块、圆角、零描边、三级字色、hover 出淡底（Qoder 口径）', () => {
    render(<TopBarIconButton icon="book" label="档案" />)
    const button = screen.getByRole('button')

    expect(button.className).toContain('size-7')
    expect(button.className).toContain('rounded-md')
    expect(button.className).not.toMatch(/(^|\s)border(-[trblxy])?(\s|$)/)
    expect(button.className).toContain('text-(--ui-text-tertiary)')
    expect(button.className).toContain('hover:bg-(--ui-row-hover-background)')
  })
})
