// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const fetchVaultAgent = vi.hoisted(() => vi.fn())
const fetchDossierTasks = vi.hoisted(() => vi.fn())
const setDossierTaskDone = vi.hoisted(() => vi.fn())
const toggleDossierPanelPin = vi.hoisted(() => vi.fn())

vi.mock('@/app/surface/api', () => ({
  fetchVaultAgent: (...args: unknown[]) => fetchVaultAgent(...args)
}))

vi.mock('./dossier-api', () => ({
  fetchDossierTasks: (...args: unknown[]) => fetchDossierTasks(...args),
  setDossierTaskDone: (...args: unknown[]) => setDossierTaskDone(...args)
}))

// 笔记那一条走的是右栏页签（现成那条路），本档只关心档案卡自己的几段。
vi.mock('@/app/chat/right-rail/notes-list', () => ({
  AgentNotesList: () => <div data-testid="agent-notes" />
}))

// 图钉那一档是真的 nanostore atom（组件用 useStore 读它），
// 其余两个动作这里只记调用次数。
vi.mock('@/store/layout', async () => {
  const { atom } = await import('nanostores')

  return {
    $dossierPanelPinned: atom(false),
    setDossierPanelMode: vi.fn(),
    toggleDossierPanelPin: (...args: unknown[]) => toggleDossierPanelPin(...args)
  }
})

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { retry: '重试' },
      surface: {
        agentDossier: '档案',
        agentLoadFailed: '档案加载失败',
        dossierCollapse: '收起',
        dossierDoneByAgent: 'Agent',
        dossierDoneByUser: '你',
        dossierGoalEmpty: '还没有写目标。',
        dossierGoalLabel: '目标',
        dossierNotesSection: '相关笔记',
        dossierPin: '固定',
        dossierProfileLoadFailed: '档案读不出来',
        dossierProgressSection: '进度',
        dossierStagesEmpty: '这个 Agent 还没有列阶段。',
        dossierStagesSection: '阶段',
        dossierStatusEmpty: '这个 Agent 还没有写现状。',
        dossierStatusLabel: '现状',
        dossierTasksEmptyDesc: '这份档案还没有按天列的任务。',
        dossierTasksLoadFailed: '清单读不出来',
        dossierToday: '今天',
        dossierUnpin: '取消固定',
        dossierUpdatedAt: (date: string) => `更新于 ${date}`,
        dossierUpdatedAtUnknown: '还没有更新记录',
        milestones: (done: number, total: number) => `${done}/${total} 个里程碑`,
        noAgentSelected: '先选一个 Agent',
        statuses: { active: '进行中' }
      }
    }
  })
}))

import { DossierPanel } from './dossier-panel'

function agent(overrides: Record<string, unknown> = {}) {
  return {
    description: '情绪追踪应用',
    displayName: 'Aura',
    docs: [],
    goal: '上架一款情绪追踪应用',
    milestones: { done: 2, total: 4 },
    profileError: '',
    profileExists: true,
    stages: [
      { detail: '罗素模型落地', done: true, id: 's1', title: '核心模型' },
      { detail: '流体水母', done: true, id: 's2', title: '交互打磨' },
      { detail: '矢量图标与测试', done: false, id: 's3', title: 'UX 打磨' },
      { detail: '软著已提交', done: false, id: 's4', title: '上架' }
    ],
    status: 'active',
    summary: '当前阶段：UX 打磨；下一步：矢量图标',
    updated: '2026-11-08',
    workPath: 'D:/data/dossiers/projects/Aura',
    ...overrides
  }
}

function days(items: Record<string, Record<string, unknown>[]> = {}) {
  return {
    days: Object.entries(items).map(([date, list]) => ({
      date,
      items: list.map(item => ({
        done: false,
        doneBy: '',
        id: `${date}#1`,
        title: '写代码',
        updatedAt: '2026-11-08T09:00:00',
        ...item
      }))
    })),
    error: null,
    exists: true
  }
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <DossierPanel agentId="Aura" />
    </QueryClientProvider>
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('档案卡 v2（五段：现状／目标／阶段／进度／相关笔记）', () => {
  it('五段同时在屏上', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    expect(await screen.findByText('阶段')).toBeTruthy()
    expect(screen.getByText('进度')).toBeTruthy()
    expect(screen.getByText('相关笔记')).toBeTruthy()
    // ① 现状：一段话。
    expect(screen.getByTestId('dossier-summary').textContent).toContain('当前阶段：UX 打磨')
  })

  it('① 现状用的是档案本体那一段，不是 md 的正文', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    const summary = await screen.findByTestId('dossier-summary')

    expect(summary.textContent).toContain('当前阶段：UX 打磨')
    expect(summary.textContent).not.toContain('## Goal')
  })

  it('③ 阶段进度条：一个节点一个阶段', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    await screen.findByTestId('dossier-stage-bar')

    for (const id of ['s1', 's2', 's3', 's4']) {
      expect(screen.getByTestId(`dossier-stage-${id}`)).toBeTruthy()
    }

    expect(screen.getByText('核心模型')).toBeTruthy()
  })

  it('③ hover 一个阶段 ⇒ 出那一个阶段的详情', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    const node = await screen.findByTestId('dossier-stage-s3')

    fireEvent.mouseEnter(node)

    const detail = screen.getByTestId('dossier-stage-detail-s3')

    expect(detail.textContent).toContain('UX 打磨')
    expect(detail.textContent).toContain('矢量图标与测试')
    // 别的那一段不该一起冒出来。
    expect(screen.queryByTestId('dossier-stage-detail-s1')).toBeNull()
  })

  it('② 目标在进度条右上角一枚图标上，hover 出目标详情', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    const goal = await screen.findByTestId('dossier-goal')

    expect(goal.getAttribute('aria-label')).toBe('目标')

    fireEvent.mouseEnter(goal)

    expect(screen.getByTestId('dossier-goal-detail').textContent).toContain('上架一款情绪追踪应用')
  })

  it('档案本体读不出来时说的是「读不出来」，不许显示成「还没写」', async () => {
    fetchVaultAgent.mockResolvedValue(agent({ profileError: 'dossier.json is not valid JSON: boom' }))
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    expect(await screen.findByText('档案读不出来')).toBeTruthy()
    expect(screen.queryByText('这个 Agent 还没有写现状。')).toBeNull()
  })

  it('还没建立档案时，现状说的是「还没写现状」', async () => {
    fetchVaultAgent.mockResolvedValue(agent({ profileExists: false, summary: '' }))
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    expect(await screen.findByText('这个 Agent 还没有写现状。')).toBeTruthy()
  })

  it('图钉在「固定／取消固定」之间切', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    const pin = await screen.findByTestId('dossier-pin')

    expect(pin.getAttribute('aria-label')).toBe('固定')

    fireEvent.click(pin)

    expect(toggleDossierPanelPin).toHaveBeenCalled()
  })

  it('按日期切换：点另一颗日期就只显示那一天的任务', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(
      days({
        '2026-11-08': [{ id: 'a', title: '今天要做的事' }],
        '2026-11-07': [{ id: 'b', title: '昨天做过的事' }]
      })
    )

    renderPanel()

    expect(await screen.findByText('今天要做的事')).toBeTruthy()

    fireEvent.click(screen.getByTestId('dossier-day-2026-11-07'))

    expect(screen.getByText('昨天做过的事')).toBeTruthy()
    expect(screen.queryByText('今天要做的事')).toBeNull()
  })

  it('勾一条 ⇒ 落盘的是「目标状态」，不是翻转', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days({ '2026-11-08': [{ id: 'a', title: '写代码' }] }))
    setDossierTaskDone.mockResolvedValue(days({ '2026-11-08': [{ id: 'a', title: '写代码', done: true }] }))

    renderPanel()

    const row = await screen.findByTestId('dossier-task-a')

    fireEvent.click(row)

    await waitFor(() => {
      expect(setDossierTaskDone).toHaveBeenCalledWith('Aura', '2026-11-08', 'a', true)
    })
  })

  it('没有清单时说的是「还没列」，不是「读不到」，也不是空白', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue({ days: [], error: null, exists: false })

    renderPanel()

    expect(await screen.findByText('这份档案还没有按天列的任务。')).toBeTruthy()
  })

  it('清单读不出来时说「读不出来」，不许显示成空清单', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockRejectedValue(new Error('boom'))

    renderPanel()

    expect(await screen.findByText('清单读不出来')).toBeTruthy()
    expect(screen.queryByText('这份档案还没有按天列的任务。')).toBeNull()
  })

  it('清单文件在但解不开时说「读不出来」——空表会骗人', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    // 文件在、后端读不出（HTTP 200，但 data.error 有值）
    fetchDossierTasks.mockResolvedValue({
      days: [],
      error: 'tasks.json is not valid JSON: boom',
      exists: true
    })

    renderPanel()

    expect(await screen.findByText('清单读不出来')).toBeTruthy()
    expect(screen.queryByText('这份档案还没有按天列的任务。')).toBeNull()
  })
})
