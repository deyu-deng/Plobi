// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { NoteMeta, NotesLibrary } from './api'

const fetchNotes = vi.fn()
const fetchNote = vi.fn()

vi.mock('./api', () => ({
  fetchNotes: () => fetchNotes(),
  fetchNote: (path: string) => fetchNote(path)
}))

vi.mock('@/components/chat/compact-markdown', () => ({
  CompactMarkdown: ({ text }: { text: string }) => <div data-testid="markdown">{text}</div>
}))

import { KnowledgeView } from './index'

const NOTE: NoteMeta = {
  path: 'Surface/notes/数学 Math/微积分.md',
  title: '微积分笔记',
  topic: '数学 Math',
  tags: ['math'],
  belongs: [],
  updated: '2026-09-20',
  chars: 120,
  excerpt: '极限与连续。'
}

const DRAWING: NoteMeta = {
  path: 'Surface/notes/工程 Engineer/图学.md',
  title: '工程图学',
  topic: '工程 Engineer',
  tags: [],
  belongs: [],
  updated: '2026-09-18',
  chars: 40,
  excerpt: '投影法。'
}

const LIBRARY: NotesLibrary = {
  root: 'X:/surface/notes',
  notes: [NOTE, DRAWING],
  topics: [
    { name: '数学 Math', count: 1 },
    { name: '工程 Engineer', count: 1 }
  ],
  tags: ['math'],
  totals: { scanned: 2, listed: 2, skipped: 0 }
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/knowledge']}>
        <KnowledgeView />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  fetchNotes.mockReset()
  fetchNote.mockReset()
  fetchNotes.mockResolvedValue(LIBRARY)
  fetchNote.mockImplementation((path: string) => {
    const note = [NOTE, DRAWING].find(row => row.path === path) as NoteMeta
    return Promise.resolve({
      path,
      fsPath: `X:/${path}`,
      text: `---\ntitle: ${note.title}\n---\n\n${note.excerpt}`,
      chars: note.chars
    })
  })
})

afterEach(cleanup)

describe('KnowledgeView', () => {
  it('lists notes and opens the first one with frontmatter stripped', async () => {
    renderView()

    await waitFor(() => expect(screen.getByText('微积分笔记')).toBeTruthy())
    expect(screen.getByText('工程图学')).toBeTruthy()

    const markdown = await screen.findByTestId('markdown')
    expect(markdown.textContent).toContain('极限与连续。')
    expect(markdown.textContent).not.toContain('title:')
  })

  it('switches notes without refetching the library', async () => {
    renderView()
    await waitFor(() => expect(screen.getByTestId('markdown')).toBeTruthy())

    fireEvent.click(screen.getByText('工程图学'))
    await waitFor(() => expect(fetchNote).toHaveBeenCalledWith('Surface/notes/工程 Engineer/图学.md'))
    expect(fetchNotes).toHaveBeenCalledTimes(1)
  })

  it('filters by search across title, excerpt and tags', async () => {
    renderView()
    await waitFor(() => expect(screen.getByText('微积分笔记')).toBeTruthy())

    fireEvent.change(screen.getByPlaceholderText('Search notes…'), { target: { value: '投影' } })
    await waitFor(() => expect(screen.queryByText('微积分笔记')).toBeNull())
    expect(screen.getAllByText('工程图学').length).toBeGreaterThan(0)
  })

  it('reports a failed read instead of an empty library', async () => {
    fetchNotes.mockRejectedValue(new Error('notes root missing'))
    renderView()

    expect(await screen.findByText('Could not read the surface')).toBeTruthy()
    expect(screen.getByText('notes root missing')).toBeTruthy()
  })
})
