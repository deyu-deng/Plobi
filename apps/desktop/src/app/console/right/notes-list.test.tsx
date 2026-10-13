import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AgentNotesView } from '@/app/console/types'

const getAgentNotes = vi.hoisted(() => vi.fn())

vi.mock('@/app/console/api', () => ({
  getAgentNotes: (...args: unknown[]) => getAgentNotes(...args)
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      console: {
        notes: {
          brainUnreachable: '脑读不到',
          emptyHowToBody: (agent: string) => `在笔记的 frontmatter 里写 belongs: [${agent}]`,
          emptyHowToTitle: '给笔记打标签',
          emptyTitle: '这条分身还没有挂笔记',
          loading: '读脑中…',
          scanned: (n: number) => `脑里已扫 ${n} 篇`,
          truncated: (n: number) => `只列前 ${n} 条`,
          unknownBelongs: (names: string) => `对不上名字：${names}`
        }
      }
    }
  })
}))

import { AgentNotesList } from './notes-list'

function view(overrides: Partial<AgentNotesView> = {}): AgentNotesView {
  return {
    agent: 'SRTP',
    budget: { max_entries: 200, max_payload_bytes: 65_536, truncated: false, used_bytes: 120 },
    category: 'projects',
    note_roots: ['Vault/notes'],
    notes: [
      { belongs: ['SRTP'], chars: 1_204, path: 'Vault/notes/数学 Math/Rust 所有权.md', title: 'Rust 所有权' }
    ],
    totals: { matched: 1, scanned: 79, skipped: 11, with_frontmatter: 68 },
    unknown_belongs: [],
    ...overrides
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AgentNotesList', () => {
  it('renders the notes that declare they belong to this agent', async () => {
    getAgentNotes.mockResolvedValue(view())

    render(<AgentNotesList agentId="SRTP" />)

    expect(await screen.findByTestId('agent-notes-list')).toBeTruthy()
    expect(screen.getByText('Rust 所有权')).toBeTruthy()
    expect(screen.getByText('1204')).toBeTruthy()
    expect(getAgentNotes).toHaveBeenCalledWith('SRTP')
  })

  it('shows brain counts when nothing is tagged yet, so empty is not silence', async () => {
    getAgentNotes.mockResolvedValue(view({ notes: [], totals: { matched: 0, scanned: 79, skipped: 11, with_frontmatter: 68 } }))

    render(<AgentNotesList agentId="SRTP" />)

    const hint = await screen.findByTestId('agent-notes-empty')

    expect(hint.textContent).toContain('79')
    expect(hint.textContent).toContain('belongs: [SRTP]')
  })

  it('surfaces an unreachable brain as an error, never as "no notes"', async () => {
    getAgentNotes.mockRejectedValue(new Error('mind not configured: MIND_ROOT is not set'))

    render(<AgentNotesList agentId="SRTP" />)

    const error = await screen.findByTestId('agent-notes-error')

    expect(error.textContent).toContain('mind not configured')
    expect(screen.queryByTestId('agent-notes-empty')).toBeNull()
  })

  it('reports the cap and the labels that name nothing registered', async () => {
    getAgentNotes.mockResolvedValue(view({
      budget: { max_entries: 200, max_payload_bytes: 65_536, truncated: true, used_bytes: 65_536 },
      unknown_belongs: ['Ghost']
    }))

    render(<AgentNotesList agentId="SRTP" />)

    expect(await screen.findByTestId('agent-notes-truncated')).toBeTruthy()
    expect(screen.getByTestId('agent-notes-unknown').textContent).toContain('Ghost')
  })
})
