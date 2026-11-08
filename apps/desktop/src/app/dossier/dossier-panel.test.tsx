// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const fetchVaultAgent = vi.hoisted(() => vi.fn())
const fetchDossierTasks = vi.hoisted(() => vi.fn())
const setDossierTaskDone = vi.hoisted(() => vi.fn())

vi.mock('@/app/surface/api', () => ({
  fetchVaultAgent: (...args: unknown[]) => fetchVaultAgent(...args)
}))

vi.mock('./dossier-api', () => ({
  fetchDossierTasks: (...args: unknown[]) => fetchDossierTasks(...args),
  setDossierTaskDone: (...args: unknown[]) => setDossierTaskDone(...args)
}))

// 笔记那一条走的是右栏页签（现成那条路），本档只关心档案卡自己的三段。
vi.mock('@/app/chat/right-rail/notes-list', () => ({
  AgentNotesList: () => <div data-testid="agent-notes" />
}))

vi.mock('@/store/layout', () => ({ setDossierPanelMode: vi.fn() }))

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
        dossierNotesSection: '相关笔记',
        dossierPlanEmpty: '这个 Agent 还没有写计划。',
        dossierPlanSection: '计划',
        dossierProgressSection: '进度',
        dossierTasksEmptyDesc: '这份档案还没有按天列的任务。',
        dossierTasksLoadFailed: '清单读不出来',
        dossierToday: '今天',
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
    docs: [
      {
        exists: true,
        filename: 'plan.md',
        key: 'plan',
        label: '计划',
        text: '---\nproject: Aura\n---\n\n## Goal\n\n上架应用',
        updated: '2026-11-08'
      }
    ],
    milestones: { done: 1, total: 4 },
    status: 'active',
    updated: '2026-11-08',
    workPath: 'D:/data/dossiers/projects/Aura',
    ...overrides
  }
}

function days(items: Record<string, unknown[]> = {}) {
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

describe('档案卡（对话卡右侧那条长卡）', () => {
  it('三段同时在屏上——不是三个抽屉', async () => {
    fetchVaultAgent.mockResolvedValue(agent())
    fetchDossierTasks.mockResolvedValue(days())

    renderPanel()

    expect(await screen.findByText('计划')).toBeTruthy()
    expect(screen.getByText('进度')).toBeTruthy()
    expect(screen.getByText('相关笔记')).toBeTruthy()
    // 计划正文不用点开就看得见（抽屉形态要点一下）。
    expect(screen.getByText(/上架应用/)).toBeTruthy()
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
