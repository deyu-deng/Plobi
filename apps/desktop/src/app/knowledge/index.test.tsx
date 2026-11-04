// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { NoteMeta, NotesLibrary } from './api'

const fetchNotes = vi.fn()
const fetchNote = vi.fn()
const saveNote = vi.fn()

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return {
    ...actual,
    fetchNotes: () => fetchNotes(),
    fetchNote: (path: string) => fetchNote(path),
    saveNote: (...args: unknown[]) => saveNote(...args)
  }
})

vi.mock('./note-markdown', async () => {
  const { stripFrontmatter } = await vi.importActual<typeof import('./outline')>('./outline')
  return {
    NoteMarkdown: ({ text }: { text: string }) => <div data-testid="markdown">{stripFrontmatter(text)}</div>
  }
})

import { KnowledgeView } from './index'

const CHAPTERS =
  '---\ntitle: 微积分笔记\n---\n\n## 第1章 极限\n\n极限与连续。\n\n### 1.1 数列极限\n\n定义。\n\n## 第2章 导数\n\n变化率。\n\n```md\n# 这是代码块里的示例，不该进目录\n```\n'

const NOTE: NoteMeta = {
  path: 'Surface/notes/数学/微积分.md',
  title: '微积分笔记',
  topic: '数学',
  tags: ['math'],
  belongs: [],
  updated: '2026-09-20',
  chars: 120,
  excerpt: '极限与连续。'
}

const DRAWING: NoteMeta = {
  path: 'Surface/notes/工具/Design/工程图学.md',
  title: '工程图学',
  topic: '工具',
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
    { name: '数学', count: 1 },
    { name: '工具', count: 1 }
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
  saveNote.mockReset()
  fetchNotes.mockResolvedValue(LIBRARY)
  saveNote.mockResolvedValue({
    path: NOTE.path,
    fsPath: 'X:/notes/数学/微积分.md',
    bytes: 120,
    created: false,
    committed: false,
    commit: '',
    detail: 'test'
  })
  fetchNote.mockImplementation((path: string) => {
    const note = [NOTE, DRAWING].find(row => row.path === path) as NoteMeta
    return Promise.resolve({
      path,
      fsPath: `X:/${path}`,
      text: path === NOTE.path ? CHAPTERS : `---\ntitle: ${note.title}\n---\n\n${note.excerpt}`,
      chars: note.chars
    })
  })
})

afterEach(cleanup)

describe('KnowledgeView', () => {
  it('shows a folder tree with the second level, not a flat topic tab', async () => {
    renderView()
    await waitFor(() => expect(screen.getByText('数学')).toBeTruthy())
    expect(screen.getByText('工具')).toBeTruthy()
    expect(screen.getByText('Design')).toBeTruthy()
    expect(screen.getAllByText('微积分笔记').length).toBeGreaterThan(0)
  })

  it('opens the first note with frontmatter stripped', async () => {
    renderView()
    const markdown = await screen.findByTestId('markdown')
    expect(markdown.textContent).toContain('极限与连续。')
    expect(markdown.textContent).not.toContain('title:')
  })

  it('builds the outline from headings and ignores fenced code blocks', async () => {
    renderView()
    await waitFor(() => expect(screen.getByText('第1章 极限')).toBeTruthy())
    expect(screen.getByText('1.1 数列极限')).toBeTruthy()
    expect(screen.getByText('第2章 导数')).toBeTruthy()
    expect(within(screen.getByTestId('note-outline')).queryByText(/这是代码块里的示例/)).toBeNull()
  })

  it('never puts the logical path on screen', async () => {
    renderView()
    await screen.findByTestId('markdown')
    expect(screen.queryByText(/Surface\/notes\//)).toBeNull()
    expect(screen.queryByText(/surface 档案/)).toBeNull()
  })

  it('switches notes without refetching the library', async () => {
    renderView()
    await screen.findByTestId('markdown')
    fireEvent.click(screen.getByText('工程图学'))
    await waitFor(() => expect(fetchNote).toHaveBeenCalledWith('Surface/notes/工具/Design/工程图学.md'))
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
    expect(await screen.findByText('Could not read notes')).toBeTruthy()
    expect(screen.getByText('notes root missing')).toBeTruthy()
  })

  it('edits the raw markdown source and saves the whole text', async () => {
    renderView()
    await screen.findByTestId('markdown')
    fireEvent.click(screen.getByText('Edit'))

    const editor = await screen.findByLabelText('Source')
    expect((editor as HTMLTextAreaElement).value).toContain('## 第1章 极限')
    fireEvent.change(editor, { target: { value: `${CHAPTERS}\n新写的一段。` } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() =>
      expect(saveNote).toHaveBeenCalledWith(
        NOTE.path,
        expect.stringContaining('新写的一段。'),
        { base: CHAPTERS, create: false }
      )
    )
  })

  it('offers a preview of the draft while editing', async () => {
    renderView()
    await screen.findByTestId('markdown')
    fireEvent.click(screen.getByText('Edit'))
    const editor = await screen.findByLabelText('Source')
    fireEvent.change(editor, { target: { value: '## 新章节\n\n预览内容' } })
    fireEvent.click(screen.getByText('Preview'))
    expect((await screen.findByTestId('markdown')).textContent).toContain('预览内容')
  })

  it('creates a note under the chosen folder', async () => {
    renderView()
    await screen.findByTestId('markdown')
    fireEvent.click(screen.getAllByTitle('New note')[0])
    const nameInput = screen.getByPlaceholderText('New note')
    fireEvent.change(nameInput, { target: { value: '线性代数' } })
    fireEvent.click(screen.getByText('Create'))

    const editor = await screen.findByLabelText('Source')
    expect((editor as HTMLTextAreaElement).value).toContain('belongs: []')
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() =>
      expect(saveNote).toHaveBeenCalledWith(
        expect.stringContaining('线性代数.md'),
        expect.any(String),
        expect.objectContaining({ create: true })
      )
    )
  })
})
