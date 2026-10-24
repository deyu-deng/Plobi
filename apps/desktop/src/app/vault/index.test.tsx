// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { VaultAgent, VaultAgentPage, VaultIndex } from './types'

const fetchVaultIndex = vi.fn()
const fetchVaultAgent = vi.fn()

vi.mock('./api', () => ({
  fetchVaultIndex: () => fetchVaultIndex(),
  fetchVaultAgent: (name: string) => fetchVaultAgent(name)
}))

// The page's job is choosing the right document and rendering its text; the
// markdown engine itself has its own surface. Keeping it out of jsdom also
// keeps this test fast and free of layout-only failures.
vi.mock('@/components/chat/compact-markdown', () => ({
  CompactMarkdown: ({ text }: { text: string }) => <div data-testid="markdown">{text}</div>
}))

import { VaultView } from './index'

function agent(overrides: Partial<VaultAgent>): VaultAgent {
  return {
    id: 'Aura',
    name: 'Aura',
    displayName: 'Aura',
    category: 'projects',
    categoryLabel: '项目',
    role: 'l2_project',
    description: '情绪追踪应用',
    homeLabel: 'Vault/projects/Aura',
    homePath: 'D:/vault/projects/Aura',
    hasHome: true,
    workPath: 'D:/projects/Aura',
    workPaths: ['D:/projects/Aura'],
    status: 'active',
    updated: '2026-09-28',
    summary: '一句摘要。',
    milestones: { done: 2, total: 4 },
    docs: [
      { key: 'plan', label: '计划', filename: 'plan.md', exists: true, chars: 120, updated: '2026-09-28', path: 'a' },
      { key: 'progress', label: '进展', filename: 'progress.md', exists: true, chars: 40, updated: '2026-09-29', path: 'b' }
    ],
    ...overrides
  }
}

const COURSE = agent({
  id: '电工电子学',
  name: '电工电子学',
  displayName: '电工电子学',
  category: 'courses',
  categoryLabel: '课程',
  description: '课程 Agent',
  homeLabel: '',
  homePath: '',
  hasHome: false,
  workPath: 'D:/Cloud/Courses/电工电子学',
  status: '',
  updated: '',
  summary: '',
  milestones: { done: 0, total: 0 },
  docs: [
    { key: 'requirements', label: '课程要求', filename: 'requirements.md', exists: false, chars: 0, updated: '', path: '' },
    { key: 'syllabus', label: '教学计划', filename: 'syllabus.md', exists: false, chars: 0, updated: '', path: '' }
  ]
})

const AGENTS = [agent({}), agent({ id: 'agenda', name: 'agenda', displayName: '管家', category: 'butler', categoryLabel: '管家', homeLabel: 'Vault/butler' }), COURSE]

function index(agents: VaultAgent[] = AGENTS): VaultIndex {
  return {
    roster: 'X:/projects.yaml',
    agents,
    totals: {
      agents: agents.length,
      withHome: agents.filter(a => a.hasHome).length,
      withoutHome: agents.filter(a => !a.hasHome).length
    }
  }
}

function pageFrom(row: VaultAgent): VaultAgentPage {
  return {
    ...row,
    docs: row.docs.map(doc => ({
      ...doc,
      fsPath: doc.exists ? `X:/vault/${doc.filename}` : '',
      text: doc.exists ? `# ${row.displayName}\n\n## 里程碑\n\n- [x] M1` : '',
      truncated: false
    }))
  }
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/vault']}>
        <VaultView />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  fetchVaultIndex.mockReset()
  fetchVaultAgent.mockReset()
  fetchVaultIndex.mockResolvedValue(index())
  fetchVaultAgent.mockImplementation((name: string) => {
    const row = AGENTS.find(a => a.id === name)
    return Promise.resolve(pageFrom(row as VaultAgent))
  })
})

afterEach(cleanup)

describe('VaultView', () => {
  it('lists every agent and opens the first one by default', async () => {
    renderView()

    await waitFor(() => expect(screen.getByText('Aura')).toBeTruthy())
    expect(screen.getByText('电工电子学')).toBeTruthy()
    expect(screen.getByText('管家')).toBeTruthy()

    await waitFor(() => expect(fetchVaultAgent).toHaveBeenCalledWith('Aura'))
    const markdown = await screen.findByTestId('markdown')
    expect(markdown.textContent).toContain('## 里程碑')
    // 计划页签是默认打开的那一份
    expect(screen.getByText('plan.md')).toBeTruthy()
  })

  it('switches document tabs without refetching the agent', async () => {
    renderView()
    await waitFor(() => expect(screen.getByTestId('markdown')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Progress' }))
    await waitFor(() => expect(screen.getByText('progress.md')).toBeTruthy())
    expect(fetchVaultAgent).toHaveBeenCalledTimes(1)
  })

  it('shows the no-home state for a course agent without a dossier folder', async () => {
    renderView()
    await waitFor(() => expect(screen.getByText('电工电子学')).toBeTruthy())

    fireEvent.click(screen.getByText('电工电子学'))
    await waitFor(() => expect(fetchVaultAgent).toHaveBeenCalledWith('电工电子学'))
    expect(await screen.findByText('No dossier folder bound yet')).toBeTruthy()
  })

  it('filters the list by search text', async () => {
    renderView()
    await waitFor(() => expect(screen.getByText('Aura')).toBeTruthy())

    fireEvent.change(screen.getByPlaceholderText('Search agents…'), { target: { value: '电工' } })
    await waitFor(() => expect(screen.queryByRole('button', { name: /Aura/ })).toBeNull())
    expect(screen.getByText('电工电子学')).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText('Search agents…'), { target: { value: '' } })
    await waitFor(() => expect(screen.getByRole('button', { name: /Aura/ })).toBeTruthy())
  })

  it('reports a failed read instead of an empty page', async () => {
    fetchVaultIndex.mockRejectedValue(new Error('backend down'))
    renderView()

    expect(await screen.findByText('Could not read the vault')).toBeTruthy()
    expect(screen.getByText('backend down')).toBeTruthy()
  })
})
