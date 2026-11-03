import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Agent } from '@/app/console/types'
import { $openConversation, closeConversation } from '@/store/conversation-view'

import { SidebarStrip, STRIP_HOVER_CLOSE_MS } from './sidebar-strip'

/**
 * 收起态那条 40px 图标条（用户 10-31 定的形状）。这一档钉两条他 11-01 报的缺陷：
 * ① 「新建 Agent」渲染成了英文单词 `add`——图标传成了字符串。屏上任何一枚都不该
 *    把内部图标名当文案印出来。
 * ② 分类图标一悬停，弹窗闪个不停。根因在 Radix 的 DropdownMenu 默认 **modal**：
 *    一打开就把 `document.body` 的 pointer-events 设成 `none`（dismissable-layer），
 *    底下那只鼠标立刻收到一次 pointerleave，于是「开 → 被关 → 再开」自己跟自己打。
 *    第二条就是照这个根因写的判据。
 *
 * 11-01 又加了三件：中段换成无文字开关、外包那一页有自己的两枚浮窗、底部入口贴着底栏。
 */

const onCreateAgent = vi.fn()
const onNavigate = vi.fn()
const onNavigateAgent = vi.fn()

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      console: {
        agentCategories: { projects: '项目', research: '研究' },
        agentsEmptyDesc: '还没有 Agent',
        backToSecretary: '回到秘书',
        expertRoleBrowser: '浏览器',
        expertRoleComputer: '电脑',
        expertRoleFile: '文件',
        expertRoleSoftware: '软件',
        expertsTitle: '专家',
        externalSessionsEmpty: '还没有会话',
        newAgent: '新建 Agent',
        offloadsTitle: '外包',
        tabAgents: '智能体',
        tabOutsourced: '外包'
      },
      sidebar: { nav: {} }
    }
  })
}))

vi.mock('../../desktop-controller-utils', () => ({
  secretaryShortName: (_id: string, name: string) => name
}))

// 底部收敛区是另一档的事（statusbar-controls 自己测），这里只留个占位。
vi.mock('@/app/shell/statusbar-controls', () => ({
  StatusbarControls: () => <div data-stub="statusbar" />
}))

const AGENT = {
  category: 'projects',
  id: 'nymo',
  name: 'Nymo',
  navigable: true,
  profile: 'nymo',
  status: 'idle',
  todayCalls: 0
} as unknown as Agent

function renderStrip(
  groups: { agents: Agent[]; category: string }[] = [{ agents: [AGENT], category: 'projects' }],
  over: Partial<React.ComponentProps<typeof SidebarStrip>> = {}
) {
  return render(
    <>
      {/* 光标路过左栏时，用户可能正在这玩意儿里打字。 */}
      <input aria-label="输入框" />
      <SidebarStrip
        activeAgentId={null}
        agentActions={[]}
        footerItems={[]}
        groups={groups}
        onCreateAgent={onCreateAgent}
        onNavigate={onNavigate}
        onNavigateAgent={onNavigateAgent}
        sharedItems={[]}
        {...over}
      />
    </>
  )
}

/**
 * React 的 `onPointerEnter` 是从原生 `pointerover` + `relatedTarget` 推出来的，
 * 光发 `pointerenter` 打不到处理器，所以两种都发。
 */
function hover(element: Element) {
  fireEvent.pointerOver(element, { bubbles: true, relatedTarget: document.body })
  fireEvent.pointerEnter(element, { relatedTarget: document.body })
}

function unhover(element: Element) {
  fireEvent.pointerOut(element, { bubbles: true, relatedTarget: document.body })
  fireEvent.pointerLeave(element, { relatedTarget: document.body })
}

beforeEach(() => {
  closeConversation()
  // 外包那一页会自己读两路数据（专家 / 外部会话）；这一档给它们一个空表。
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: {
      ...(window.plobiDesktop || {}),
      api: vi.fn().mockResolvedValue({ data: { experts: [], projects: [], reason: '', sessions: [] }, ok: true })
    }
  })
})

afterEach(() => {
  cleanup()
  closeConversation()
  document.body.style.pointerEvents = ''
  vi.clearAllMocks()
})

describe('左栏收起态图标条', () => {
  it('「新建 Agent」是一枚图标，不是文字', () => {
    renderStrip()

    const button = screen.getByRole('button', { name: '新建 Agent' })

    expect(button.textContent, '屏上印了字：图标名被当文案渲染').toBe('')
    expect(button.querySelector('.codicon-add'), '没有 codicon-add 这枚图标').not.toBeNull()
  })

  it('悬停弹出的分类面板不许锁住全页的 pointer-events', async () => {
    renderStrip()

    hover(screen.getByRole('button', { name: '项目' }))

    await screen.findByRole('menu')

    // 锁了 = 触发键当场收到 pointerleave = 弹窗开开关关闪个不停。
    expect(document.body.style.pointerEvents, 'body 被锁成 pointer-events:none').toBe('')
  })

  it('指针从图标移到面板上不算离开，出了两边才关', async () => {
    renderStrip()

    const trigger = screen.getByRole('button', { name: '项目' })
    hover(trigger)
    const menu = await screen.findByRole('menu')

    // 真实顺序：浏览器先给旧位置 pointerout，再给新位置 pointerover。
    unhover(trigger)
    hover(menu)
    await new Promise(resolve => {
      setTimeout(resolve, STRIP_HOVER_CLOSE_MS + 150)
    })
    expect(menu.getAttribute('data-state'), '移到面板上却被关掉了').toBe('open')

    unhover(menu)
    await waitFor(() => {
      const next = screen.queryByRole('menu')

      expect(next === null || next.getAttribute('data-state') === 'closed').toBe(true)
    })
  })

  it('悬停开的面板不许把焦点从输入框抢走', async () => {
    renderStrip()

    const input = screen.getByLabelText('输入框')

    input.focus()
    hover(screen.getByRole('button', { name: '项目' }))

    // Radix 的 FocusScope 在挂载后的下一帧抓焦点，所以这里要多等几帧。
    await screen.findByRole('menu')
    await new Promise(resolve => {
      setTimeout(resolve, 120)
    })

    expect(document.activeElement, '焦点被面板抢走了：接着打字会打进方向键搜索').toBe(input)
  })

  it('整条同时只开一个分类面板', async () => {
    renderStrip([
      { agents: [AGENT], category: 'projects' },
      { agents: [AGENT], category: 'research' }
    ])

    hover(screen.getByRole('button', { name: '项目' }))
    await screen.findByRole('menu')

    // 手一抖划到下一类：旧的关掉、新的开来，不许两份叠在同一个位置。
    hover(screen.getByRole('button', { name: '研究' }))

    await waitFor(() => {
      const opened = screen.queryAllByRole('menu').filter(item => item.getAttribute('data-state') === 'open')

      expect(opened).toHaveLength(1)
      expect(opened[0]?.textContent).toContain('研究')
    })
  })
})

describe('收起态：中段开关与外包两枚浮窗（用户 11-01）', () => {
  it('中段是一枚**无文字**的开关，点一下就把名单换成外包', () => {
    const onChangeMiddleView = vi.fn()

    renderStrip(undefined, { activeAgentId: 'l2-a', middleView: 'agents', onChangeMiddleView })

    const toggle = screen.getByRole('switch')

    expect(toggle.textContent, '开关上印了字').toBe('')
    expect(toggle.getAttribute('aria-checked')).toBe('false')

    fireEvent.click(toggle)
    expect(onChangeMiddleView).toHaveBeenCalledWith('outsourced')
  })

  it('停在「外包」那一页时，中段是专家与外包两枚图标（不是五类 Agent）', async () => {
    renderStrip(undefined, { activeAgentId: 'l2-a', middleView: 'outsourced', onChangeMiddleView: vi.fn() })

    expect(screen.getByRole('button', { name: '专家' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '外包' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '项目' })).toBeNull()
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true')
  })

  it('悬浮「专家」就能挑一个专家，不用先展开左栏', async () => {
    const api = window.plobiDesktop.api as unknown as ReturnType<typeof vi.fn>

    api.mockImplementation(({ path }: { path: string }) =>
      path.endsWith('/experts')
        ? Promise.resolve({
          data: {
            experts: [{
              goal: '整理三份资料', id: 'kid-1', messages: 2, model: 'glm', parent_session_id: 'p',
              role: 'file', started_at: 1, status: 'running', tool_calls: 1
            }]
          },
          ok: true
        })
        : Promise.resolve({ data: { experts: [], projects: [], reason: '', sessions: [] }, ok: true })
    )

    renderStrip(undefined, { activeAgentId: 'l2-a', middleView: 'outsourced', onChangeMiddleView: vi.fn() })

    hover(screen.getByRole('button', { name: '专家' }))

    // 浮窗里是四类角色的名册，没跑过的灰着。
    const menu = await screen.findByRole('menu')

    expect(menu.textContent).toContain('文件')
    expect(menu.textContent).toContain('电脑')

    fireEvent.click(await screen.findByRole('menuitem', { name: /文件/ }))

    await waitFor(() =>
      expect($openConversation.get()).toEqual(expect.objectContaining({ kind: 'expert', sessionId: 'kid-1' }))
    )
  })

  it('悬浮「外包」就能挑一条会话，点它是交给主区那张卡片', async () => {
    const api = window.plobiDesktop.api as unknown as ReturnType<typeof vi.fn>

    api.mockImplementation(({ path }: { path: string }) =>
      path.endsWith('/external-sessions')
        ? Promise.resolve({
          data: {
            projects: [],
            reason: '',
            sessions: [{
              id: 's-1', project: '', related: true, sendable: true,
              title: '先读 README', updated_at: '2026-10-27T10:00:00.000Z', vendor: 'qoder'
            }]
          },
          ok: true
        })
        : Promise.resolve({ data: { experts: [], projects: [], reason: '', sessions: [] }, ok: true })
    )

    renderStrip(undefined, { activeAgentId: 'l2-a', middleView: 'outsourced', onChangeMiddleView: vi.fn() })

    hover(screen.getByRole('button', { name: '外包' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /先读 README/ }))

    await waitFor(() =>
      expect($openConversation.get()).toEqual(
        expect.objectContaining({ kind: 'external', sessionId: 's-1', vendor: 'qoder' })
      )
    )
  })

  it('Capabilities / Artifacts 在底栏上方单独一块——收起前后不跳位置', () => {
    const { container } = renderStrip(undefined, {
      agentActions: [
        { icon: 'extensions', id: 'capabilities', label: '能力', onClick: vi.fn() },
        { icon: 'archive', id: 'artifacts', label: '产物', onClick: vi.fn() }
      ]
    })

    const blocks = Array.from(container.querySelectorAll('[data-slot="sidebar-strip"] > div'))

    // 三块自上而下：可滚动的图标区 / 本 Agent 的入口 / 底部收敛区。
    expect(blocks).toHaveLength(3)
    expect(blocks[0]?.getAttribute('data-stub')).toBeNull()
    expect(blocks[1]?.textContent).toBe('')
    expect(blocks[1]?.querySelectorAll('button')).toHaveLength(2)
    expect(blocks[2]?.querySelector('[data-stub="statusbar"]')).not.toBeNull()
  })
})
