// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Agent } from '@/app/console/types'

import { AgentCategoryGroup } from './agent-category-group'

// 这三个格子不归本测试管：菜单（另有它自己那份）、会话行（另有它自己那份）、
// i18n（组件本身不取文案，但 session-row 会）。
vi.mock('./agent-actions-menu', () => ({
  AgentActionsMenu: () => null
}))

vi.mock('./session-row', () => ({
  SidebarSessionRow: () => null
}))

const agent = (over: Partial<Agent> = {}): Agent =>
  ({
    category: 'projects',
    id: 'aura',
    name: 'Aura',
    profile: 'l2-aura',
    status: 'idle',
    ...over
  }) as Agent

function renderGroup(over: Partial<Parameters<typeof AgentCategoryGroup>[0]> = {}) {
  const onNavigateAgent = vi.fn()

  render(
    <AgentCategoryGroup
      activeAgentId={null}
      activeSessionId={null}
      agents={[agent()]}
      category="projects"
      hideSessions
      label="项目"
      onArchiveSession={vi.fn()}
      onDeleteSession={vi.fn()}
      onNavigateAgent={onNavigateAgent}
      onResumeSession={vi.fn()}
      onTogglePin={vi.fn()}
      sessionsByProfile={new Map()}
      workingSessionIdSet={new Set()}
      {...over}
    />
  )

  return { onNavigateAgent }
}

afterEach(() => {
  cleanup()
})

describe('AgentCategoryGroup — Agent 行点得动（用户 10-22「左边的 Agent 有点击不了了」）', () => {
  it('点一行就打开那个 Agent 的对话', () => {
    const { onNavigateAgent } = renderGroup()

    fireEvent.click(screen.getByRole('button', { name: 'Aura' }))

    expect(onNavigateAgent).toHaveBeenCalledWith('aura')
  })

  it('创建时选的图标要画出来（颜色染它）', () => {
    renderGroup({ agents: [agent({ color: 'hsl(210 68% 58%)', icon: 'robot' })] })

    // Codicon 渲染成 <i class="codicon codicon-robot">；颜色走内联 style。
    const icon = document.querySelector('.codicon-robot')
    expect(icon).not.toBeNull()
    // jsdom 会把 hsl() 归一成 rgb()——断言的是「颜色真的落到那个元素上」。
    expect((icon?.parentElement as HTMLElement).style.color).toBe('rgb(75, 148, 221)')
  })

  it('没选图标才回到状态点（有颜色就染那个点）', () => {
    renderGroup({ agents: [agent({ color: 'hsl(45 85% 55%)' })] })

    // 在**这一行里面**找：整个 document 里第一个 aria-hidden 的 span 可能是
    // 分组标题里那个（DOM 更靠前），那就断言不到点本身。
    const row = screen.getByRole('button', { name: 'Aura' })
    const dot = row.querySelector('span[aria-hidden="true"]')
    expect((dot as HTMLElement | null)?.style.backgroundColor).toBe('rgb(238, 189, 43)')
  })

  it('这个 Agent 在干活就挂那条光条（后端 working 说了算，跨家）', () => {
    renderGroup({ agents: [agent({ working: true })] })

    expect(document.querySelector('.arc-border')).not.toBeNull()
  })

  it('没在干活就没有光条（它是个指示器，不是装饰）', () => {
    renderGroup()

    expect(document.querySelector('.arc-border')).toBeNull()
  })

  it('点不动的行不许调跳转（后端 navigable=false）', () => {
    const { onNavigateAgent } = renderGroup({
      agents: [agent({ navigable: false, notNavigableReason: '还没在这台机器上落地' })]
    })

    fireEvent.click(screen.getByRole('button', { name: 'Aura' }))

    expect(onNavigateAgent).not.toHaveBeenCalled()
  })
})
