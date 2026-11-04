import { useQuery } from '@tanstack/react-query'
import { type ReactNode, type UIEvent, useEffect, useMemo, useRef, useState } from 'react'

import { ListColumn, MasterDetail } from '@/app/master-detail'
import { PageSearchShell } from '@/app/page-search-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { EmptyState } from '@/components/ui/empty-state'
import { Textarea } from '@/components/ui/textarea'
import { PageLoader } from '@/components/page-loader'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { NOTES_PREFIX, fetchNote, fetchNotes, saveNote } from './api'
import type { NoteMeta } from './api'
import { NoteMarkdown } from './note-markdown'
import { buildNoteTree, noteTemplate, parseOutline } from './outline'
import type { NoteTreeNode } from './outline'

function matches(note: NoteMeta, needle: string): boolean {
  if (!needle) return true
  return [note.title, note.excerpt, note.topic, ...note.tags].join('\n').toLowerCase().includes(needle.toLowerCase())
}

function breadcrumb(path: string): string {
  const parts = path.startsWith(NOTES_PREFIX) ? path.slice(NOTES_PREFIX.length) : path
  return parts.split('/').slice(0, -1).join(' / ')
}

function NoteRow({ active, note, onSelect }: { active: boolean; note: NoteMeta; onSelect: () => void }) {
  return (
    <button
      className={cn(
        'flex w-full items-center rounded-md px-2 py-1 text-left transition-colors',
        'hover:bg-(--ui-control-hover-background)/60',
        active && 'bg-(--ui-control-hover-background)'
      )}
      onClick={onSelect}
      type="button"
    >
      <span className={cn('min-w-0 truncate text-[0.72rem]', active && 'font-medium')}>{note.title}</span>
    </button>
  )
}

export function KnowledgeView() {
  const { t } = useI18n()
  const v = t.surface
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const [editing, setEditing] = useState(false)
  const [view, setView] = useState<'source' | 'preview'>('source')
  const [draft, setDraft] = useState('')
  const [base, setBase] = useState('')
  const [isNew, setIsNew] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newParent, setNewParent] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [activeHeading, setActiveHeading] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const library = useQuery({ queryKey: ['surface-notes'], queryFn: fetchNotes, staleTime: 0 })
  const notes = library.data?.notes ?? []
  const page = useQuery({
    queryKey: ['surface-note', selected],
    queryFn: () => fetchNote(selected as string),
    enabled: Boolean(selected) && !isNew,
    staleTime: 0
  })

  const visible = useMemo(() => notes.filter(note => matches(note, query.trim())), [notes, query])
  const tree = useMemo(() => buildNoteTree(visible), [visible])
  const searching = query.trim().length > 0

  useEffect(() => {
    if (isNew || (selected && visible.some(note => note.path === selected))) return
    setSelected(visible[0]?.path ?? null)
  }, [isNew, selected, visible])

  const activeNote = notes.find(note => note.path === selected) ?? null
  const sourceText = editing ? draft : (page.data?.text ?? '')
  const outline = useMemo(() => parseOutline(sourceText), [sourceText])
  const dirty = editing && draft !== base

  function selectNote(path: string) {
    setSelected(path)
    setEditing(false)
    setIsNew(false)
    setError(null)
    setView('source')
    setActiveHeading(null)
    setDraft('')
    setBase('')
  }

  function startEdit() {
    const text = page.data?.text ?? ''
    setDraft(text)
    setBase(text)
    setEditing(true)
    setView('source')
    setError(null)
  }

  function cancelEdit() {
    setEditing(false)
    setError(null)
    setDraft('')
    setBase('')
    setView('source')
    if (isNew) {
      setIsNew(false)
      setSelected(visible[0]?.path ?? null)
    }
  }

  function startNew() {
    const name = newName.trim()
    if (!name) return
    const folder = newParent ?? ''
    const path = `${NOTES_PREFIX}${folder ? `${folder}/` : ''}${name}.md`
    setSelected(path)
    setIsNew(true)
    setEditing(true)
    setDraft(noteTemplate(name))
    setBase('')
    setView('source')
    setError(null)
    setNewParent(null)
    setNewName('')
  }

  async function save() {
    if (!selected || saving) return
    setSaving(true)
    setError(null)
    try {
      await saveNote(selected, draft, { base, create: isNew })
      setIsNew(false)
      setEditing(false)
      setBase(draft)
      setError(null)
      await Promise.all([library.refetch(), page.refetch()])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  const saveRef = useRef<() => void>(() => {})
  saveRef.current = () => void save()
  useEffect(() => {
    if (!editing) return
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        saveRef.current()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [editing])

  function toggleFolder(key: string) {
    setCollapsed(previous => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function jumpTo(id: string) {
    const element = document.getElementById(id)
    const container = scrollRef.current
    if (element && container) {
      const offset = element.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop
      container.scrollTo({ top: offset - 8, behavior: 'smooth' })
    }
    setActiveHeading(id)
  }

  function onScroll(event: UIEvent<HTMLDivElement>) {
    const container = event.currentTarget
    const line = container.getBoundingClientRect().top + 24
    let current: string | null = null
    for (const item of outline) {
      const element = document.getElementById(item.id)
      if (!element) continue
      if (element.getBoundingClientRect().top <= line) current = item.id
      else break
    }
    setActiveHeading(current)
  }

  const renderFolder = (node: NoteTreeNode<NoteMeta>, depth: number): ReactNode => {
    const open = searching || !collapsed.has(node.key)
    return (
      <div key={node.key}>
        <div className="group flex items-center gap-0.5" style={{ paddingLeft: depth * 10 }}>
          <button
            className="flex min-w-0 flex-1 items-center gap-1 rounded px-1 py-1 text-left hover:bg-(--ui-control-hover-background)/50"
            onClick={() => toggleFolder(node.key)}
            type="button"
          >
            <span className="w-2 shrink-0 text-[0.6rem] text-muted-foreground/60">{open ? '▾' : '▸'}</span>
            <span className="truncate text-[0.72rem] font-medium">{node.name}</span>
          </button>
          <button
            aria-label={v.newNote}
            className="shrink-0 rounded px-1 text-[0.7rem] text-muted-foreground/50 opacity-0 hover:text-foreground group-hover:opacity-100 focus:opacity-100"
            onClick={() => {
              setNewParent(node.key)
              setNewName('')
            }}
            title={v.newNote}
            type="button"
          >
            +
          </button>
        </div>
        {open && (
          <>
            {node.folders.map(child => renderFolder(child, depth + 1))}
            {node.notes.map(note => (
              <div key={note.path} style={{ paddingLeft: (depth + 1) * 10 + 12 }}>
                <NoteRow active={note.path === selected} note={note} onSelect={() => selectNote(note.path)} />
              </div>
            ))}
          </>
        )}
      </div>
    )
  }

  const noteBody = (() => {
    if (editing && view === 'source') {
      return (
        <Textarea
          aria-label={v.source}
          className="min-h-0 flex-1 resize-none font-mono text-[0.78rem] leading-6"
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
              event.preventDefault()
              saveRef.current()
            }
          }}
          spellCheck={false}
          value={draft}
        />
      )
    }
    if (editing && view === 'preview') {
      return <NoteMarkdown text={draft} />
    }
    if (page.isError) {
      return <EmptyState description={page.error instanceof Error ? page.error.message : undefined} title={v.loadFailed} />
    }
    // selected 还没落定时 query 是 disabled 的，此时 isLoading 会是 false——
    // 只认 isLoading 会先渲染一篇空正文，闪一帧还得让下游自己防。
    if (!page.data) {
      return <PageLoader label={v.loading} />
    }
    return (
      <article className="space-y-4">
        <header className="space-y-2">
          <h1 className="text-lg font-semibold leading-tight">{activeNote?.title ?? ''}</h1>
          <div className="flex flex-wrap items-center gap-1.5 text-[0.66rem] text-muted-foreground/70">
            {activeNote && <Badge variant="outline">{activeNote.topic}</Badge>}
            {activeNote?.tags.slice(0, 4).map(tag => <span key={tag}>#{tag}</span>)}
            <span className="ml-auto tabular-nums">
              {v.updated} {activeNote?.updated || '—'}
            </span>
          </div>
        </header>
        <NoteMarkdown text={sourceText} />
      </article>
    )
  })()

  const body = library.isError ? (
    <EmptyState description={library.error instanceof Error ? library.error.message : undefined} title={v.loadFailed} />
  ) : library.isLoading ? (
    <PageLoader label={v.loading} />
  ) : visible.length === 0 && !isNew ? (
    <EmptyState description={v.emptyDesc} title={notes.length ? v.noMatchTitle : v.emptyTitle} />
  ) : (
    <MasterDetail split="wide">
      <ListColumn>
        {tree.folders.map(node => renderFolder(node, 0))}
        {tree.loose.map(note => (
          <NoteRow active={note.path === selected} key={note.path} note={note} onSelect={() => selectNote(note.path)} />
        ))}
        {newParent !== null && (
          <div className="mt-2 flex items-center gap-1 px-1">
            <input
              aria-label={v.newNote}
              autoFocus
              className="min-w-0 flex-1 rounded border border-(--ui-stroke-tertiary) bg-background px-1.5 py-1 text-[0.72rem] outline-none"
              onChange={event => setNewName(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter') startNew()
                if (event.key === 'Escape') setNewParent(null)
              }}
              placeholder={v.newNote}
              value={newName}
            />
            <Button onClick={startNew} variant="ghost">
              {v.create}
            </Button>
          </div>
        )}
      </ListColumn>

      <main className="flex min-h-0 flex-col overflow-hidden">
        <div className="flex min-h-0 flex-1">
          <div
            className={cn('min-w-0 flex-1 px-5 py-4 [scrollbar-gutter:stable]', editing && view === 'source' ? 'flex overflow-hidden' : 'overflow-y-auto overscroll-contain')}
            onScroll={editing && view === 'source' ? undefined : onScroll}
            ref={scrollRef}
          >
            <div className={cn('mx-auto w-full max-w-2xl', editing && view === 'source' && 'flex h-full flex-col')}>
              {!editing && activeNote && (
                <div className="mb-3 truncate text-[0.62rem] text-muted-foreground/50" title={activeNote.path}>
                  {breadcrumb(activeNote.path)}
                </div>
              )}
              {noteBody}
            </div>
          </div>

          <aside className="w-40 shrink-0 overflow-y-auto border-l border-(--ui-stroke-tertiary) px-2 py-3" data-testid="note-outline">
            <div className="mb-2 px-1.5 text-[0.62rem] text-muted-foreground/60">{v.outline}</div>
            {outline.length === 0 ? (
              <div className="px-1.5 text-[0.62rem] text-muted-foreground/40">—</div>
            ) : (
              outline.map(item => (
                <button
                  className={cn(
                    'block w-full truncate rounded px-1.5 py-1 text-left text-[0.66rem] hover:bg-(--ui-control-hover-background)/60',
                    item.level <= 2 ? 'font-medium' : 'text-muted-foreground/80',
                    activeHeading === item.id && 'bg-(--ui-control-hover-background)'
                  )}
                  key={item.id}
                  onClick={() => jumpTo(item.id)}
                  style={{ paddingLeft: 6 + Math.max(0, item.level - 2) * 8 }}
                  title={item.text}
                  type="button"
                >
                  {item.text}
                </button>
              ))
            )}
          </aside>
        </div>

        <footer className="shrink-0 border-t border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-5 py-2">
          <div className="mx-auto flex max-w-2xl items-center gap-2">
            {editing ? (
              <>
                <Button aria-pressed={view === 'source'} onClick={() => setView('source')} variant="ghost">
                  {v.source}
                </Button>
                <Button aria-pressed={view === 'preview'} onClick={() => setView('preview')} variant="ghost">
                  {v.preview}
                </Button>
                {dirty && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" title={v.unsaved} />}
                {error && (
                  <span className="min-w-0 truncate text-[0.65rem] text-destructive" title={error}>
                    {v.saveFailed}
                  </span>
                )}
                <span className="ml-auto" />
                <Button onClick={cancelEdit} variant="ghost">
                  {v.cancel}
                </Button>
                <Button disabled={saving} onClick={() => void save()} variant="ghost">
                  {saving ? v.saving : v.save}
                </Button>
              </>
            ) : (
              <>
                <span className="ml-auto" />
                <Button onClick={startEdit} variant="ghost">
                  <Codicon name="edit" size="0.85rem" />
                  {v.edit}
                </Button>
              </>
            )}
          </div>
        </footer>
      </main>
    </MasterDetail>
  )

  return (
    <PageSearchShell
      onSearchChange={setQuery}
      searchPlaceholder={v.searchPlaceholder}
      searchTrailingAction={
        <Button
          aria-label={v.refresh}
          onClick={() => {
            void library.refetch()
            if (selected) void page.refetch()
          }}
          size="icon-titlebar"
          title={v.refresh}
          variant="ghost"
        >
          <Codicon name="refresh" size="0.85rem" spinning={library.isFetching || page.isFetching} />
        </Button>
      }
      searchValue={query}
    >
      {body}
    </PageSearchShell>
  )
}
