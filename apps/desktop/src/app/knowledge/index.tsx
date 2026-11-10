import { useQuery } from '@tanstack/react-query'
import { type ReactNode, type UIEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { PageSearchShell } from '@/app/page-search-shell'
import { Pane, PaneMain, PaneShell } from '@/components/pane-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from '@/components/ui/context-menu'
import { EmptyState } from '@/components/ui/empty-state'
import { Textarea } from '@/components/ui/textarea'
import { PageLoader } from '@/components/page-loader'
import { translateNow, useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'

import { deleteNote, fetchNote, fetchNotes, NOTES_PREFIX, renameNote, saveNote } from './api'
import type { NoteMeta } from './api'
import { HoverPreview } from './hover-preview'
import { NoteMarkdown, jumpToAnchor, makeLinkIndex } from './note-markdown'
import { buildNoteTree, noteTemplate, parseOutline } from './outline'
import type { NoteTreeNode } from './outline'
import { NoteSwitcher, useNoteSwitcherEscape, useNoteSwitcherHotkey } from './switcher'
import { NoteTabs } from './tabs'

function matches(note: NoteMeta, needle: string): boolean {
  if (!needle) return true
  return [note.title, note.excerpt, note.topic, ...note.tags].join('\n').toLowerCase().includes(needle.toLowerCase())
}

function breadcrumb(path: string): string {
  const parts = path.startsWith(NOTES_PREFIX) ? path.slice(NOTES_PREFIX.length) : path
  return parts.split('/').slice(0, -1).join(' / ')
}

/**
 * 行内改名：那一行变成输入框（初值是这篇的标题，名字部分预选中）。
 * Enter 提交、Esc 取消、失焦提交——与右侧栏那一套行内改名同形。
 */
function InlineNoteRename({
  initialValue,
  onDone
}: {
  initialValue: string
  /** 名字是提交，null 是取消。 */
  onDone: (name: string | null) => void
}) {
  const { t } = useI18n()
  const [value, setValue] = useState(initialValue)
  const inputRef = useRef<HTMLInputElement>(null)
  // Enter 之后的那次 blur 不许重复提交：只认第一次收尾。
  const done = useRef(false)
  // 刚挂上就 blur（右键菜单收尾、焦点乱动）——这一小段里把焦点抢回来。
  const mountedAt = useRef(Date.now())

  useEffect(() => {
    const input = inputRef.current
    if (!input) {
      return
    }

    // 名字部分预选中：扩展名不算名字。
    const dot = input.value.lastIndexOf('.')
    input.setSelectionRange(0, dot > 0 ? dot : input.value.length)
  }, [])

  function finish(commit: boolean) {
    if (done.current) {
      return
    }

    done.current = true
    const next = value.trim()

    if (commit && next && next !== initialValue) {
      onDone(next)

      return
    }

    onDone(null)
  }

  return (
    <input
      aria-label={t.fileMenu.renameLabel}
      autoCapitalize="off"
      autoComplete="off"
      autoCorrect="off"
      autoFocus
      className="w-full min-w-0 rounded-sm border border-(--ui-stroke-tertiary) bg-background px-1.5 py-1 text-[0.72rem] outline-none"
      onBlur={event => {
        if (Date.now() - mountedAt.current < 250) {
          event.currentTarget.focus()

          return
        }

        finish(true)
      }}
      onChange={event => setValue(event.target.value)}
      onClick={event => event.stopPropagation()}
      onDoubleClick={event => event.stopPropagation()}
      onFocus={event => {
        const dot = event.currentTarget.value.lastIndexOf('.')
        event.currentTarget.setSelectionRange(0, dot > 0 ? dot : event.currentTarget.value.length)
      }}
      onKeyDown={event => {
        event.stopPropagation()

        if (event.key === 'Enter') {
          event.preventDefault()
          finish(true)
        } else if (event.key === 'Escape') {
          event.preventDefault()
          finish(false)
        }
      }}
      ref={inputRef}
      spellCheck={false}
      value={value}
    />
  )
}

function NoteRow({
  active,
  note,
  onDelete,
  onPreview,
  onPreviewEnd,
  onRename,
  onRenameDone,
  onSelect,
  renaming
}: {
  active: boolean
  note: NoteMeta
  /** 右键菜单里的「删除」。 */
  onDelete: () => void
  /** 悬停时回调（带上指针位置，预览卡片要跟着指针走）。 */
  onPreview?: (note: NoteMeta, position: { x: number; y: number }) => void
  /** 指针离开这一行——预览卡片该自己走掉了。 */
  onPreviewEnd?: () => void
  /** 右键菜单里的「重命名…」：这一行换成行内输入框。 */
  onRename: () => void
  /** 行内改名的结果：名字是提交，null 是取消。 */
  onRenameDone: (name: string | null) => void
  onSelect: () => void
  /** 这一行正在改名（输入框占着这一格）。 */
  renaming: boolean
}) {
  const { t } = useI18n()
  const m = t.fileMenu

  if (renaming) {
    return <InlineNoteRename initialValue={note.title} onDone={onRenameDone} />
  }

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <button
          className={cn(
            'flex w-full items-center rounded-md px-2 py-1 text-left transition-colors',
            'hover:bg-(--ui-control-hover-background)/60',
            active && 'bg-(--ui-control-hover-background)'
          )}
          onClick={onSelect}
          onMouseEnter={event => {
            const rect = event.currentTarget.getBoundingClientRect()
            onPreview?.(note, { x: rect.right + 8, y: Math.min(rect.top, window.innerHeight - 220) })
          }}
          onMouseLeave={onPreviewEnd}
          type="button"
        >
          <span className={cn('min-w-0 truncate text-[0.72rem]', active && 'font-medium')}>{note.title}</span>
        </button>
      </ContextMenuTrigger>
      {/* 不把焦点还给那一行：行内输入框刚挂上就被"焦点还回来"blur 掉。 */}
      <ContextMenuContent onCloseAutoFocus={event => event.preventDefault()}>
        <ContextMenuItem onSelect={onRename}>{m.rename}</ContextMenuItem>
        <ContextMenuItem onSelect={onDelete} variant="destructive">
          {m.delete}
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
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
  // 右键菜单那一格：正在改名的笔记、等着确认删除的笔记。
  const [renamingPath, setRenamingPath] = useState<string | null>(null)
  const [deletingNote, setDeletingNote] = useState<NoteMeta | null>(null)
  // 改名/删除还在路上：选中哪一格由我们自己定，下面那段"自动选第一篇"别来抢。
  const [pendingPath, setPendingPath] = useState<string | null>(null)

  // 多标签页：开着哪几篇笔记。关掉最后一个时回到「无标签」态（空态）。
  const [openTabs, setOpenTabs] = useState<string[]>([])
  // 快速切换器（mod+o）
  const [switcherOpen, setSwitcherOpen] = useState(false)
  // 悬浮预览：卡片看的是哪一篇（hoverNote）、指针在哪（hoverPos）、
  // 指针此刻还在不在树行上（rowHover）——最后这一格是「卡片会自己走掉」的开关。
  const [hoverNote, setHoverNote] = useState<NoteMeta | null>(null)
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number } | null>(null)
  const [rowHover, setRowHover] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
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

  // wikilink 解析用的索引：库里有哪些笔记（按文件名与标题两套键）。
  const linkIndex = useMemo(() => makeLinkIndex(notes), [notes])

  useEffect(() => {
    // 改名/删除还在路上：选中哪一格由我们自己定。
    if (pendingPath) {
      return
    }

    if (isNew || (selected && visible.some(note => note.path === selected))) {
      return
    }

    const first = visible[0]?.path ?? null
    setSelected(first)
    // 初始那一格也开成标签——Obsidian 的手感：打开过的笔记就留在标签栏上。
    if (first) setOpenTabs(previous => (previous.includes(first) ? previous : [...previous, first]))
  }, [isNew, pendingPath, selected, visible])

  const activeNote = notes.find(note => note.path === selected) ?? null
  const sourceText = editing ? draft : (page.data?.text ?? '')
  const outline = useMemo(() => parseOutline(sourceText), [sourceText])
  const dirty = editing && draft !== base

  const tabs = useMemo(
    () =>
      openTabs
        .map(path => notes.find(note => note.path === path))
        .filter((note): note is NoteMeta => Boolean(note)),
    [notes, openTabs]
  )

  // 悬停目录树的一篇：记下它和指针位置（预览卡片跟着指针走）。
  const previewNote = useCallback((note: NoteMeta, position: { x: number; y: number }) => {
    setHoverNote(note)
    setHoverPos(position)
    setRowHover(true)
  }, [])

  // 指针离开那一行：卡片自己会收起（留一小段宽限，指针才挪得到卡片上）。
  const endPreview = useCallback(() => setRowHover(false), [])

  // 卡片自己收起（Esc／移开／点「打开」之后）：这一格清干净。
  const closePreview = useCallback(() => {
    setHoverNote(null)
    setHoverPos(null)
    setRowHover(false)
  }, [])

  function selectNote(path: string) {
    setSelected(path)
    setEditing(false)
    setIsNew(false)
    setError(null)
    setView('source')
    setActiveHeading(null)
    setDraft('')
    setBase('')
    // 选中的笔记自动开一个标签（Obsidian 的手感：打开过就留在标签栏上）。
    setOpenTabs(previous => (previous.includes(path) ? previous : [...previous, path]))
  }

  function closeTab(path: string) {
    setOpenTabs(previous => {
      const next = previous.filter(item => item !== path)
      // 关掉当前这一格时，落到剩下的最后一格；一格都不剩就清空选中。
      if (path === selected) {
        const fallback = next[next.length - 1] ?? null
        setSelected(fallback)
        setEditing(false)
        setIsNew(false)
        setError(null)
        setDraft('')
        setBase('')
      }
      return next
    })
  }

  function openFromSwitcher(path: string) {
    selectNote(path)
  }

  // wikilink：同篇锚点跳转
  function openAnchor(anchor: string) {
    const container = scrollRef.current
    if (!container) return
    if (jumpToAnchor(container, anchor)) setActiveHeading(anchor)
  }

  // wikilink：跳到另一篇笔记（开成新标签，不打断当前这页的阅读位置）
  function openNote(path: string) {
    selectNote(path)
  }

  const openAnchorRef = useRef(openAnchor)
  openAnchorRef.current = openAnchor
  const openNoteRef = useRef(openNote)
  openNoteRef.current = openNote

  useNoteSwitcherHotkey(switcherOpen, setSwitcherOpen)
  useNoteSwitcherEscape(switcherOpen, () => setSwitcherOpen(false))

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

  function startRename(path: string) {
    setRenamingPath(path)
  }

  // 行内改名的结果：名字是提交，null 是取消。
  async function finishRename(path: string, name: string | null) {
    setRenamingPath(null)
    if (!name) {
      return
    }

    setPendingPath(path)
    try {
      const result = await renameNote(path, name)
      const fresh = await library.refetch()
      const renamed = fresh.data?.notes.find(note => note.path === result.new_path)
      // 改完名就是另一条路径了：选中它。
      if (renamed) {
        selectNote(renamed.path)
      }
    } catch (cause) {
      notifyError(cause, translateNow('errors.genericFailure'))
    } finally {
      setPendingPath(null)
    }
  }

  function requestDelete(note: NoteMeta) {
    setDeletingNote(note)
  }

  // 确认之后才动盘。选中哪一格由下面那段"自动选第一篇"接手（删掉的那篇不在清单里了）。
  async function confirmDelete() {
    const target = deletingNote
    if (!target) {
      return
    }

    setPendingPath(target.path)
    try {
      await deleteNote(target.path)
      await library.refetch()
    } finally {
      setPendingPath(null)
    }
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
                <NoteRow
                  active={note.path === selected}
                  note={note}
                  onDelete={() => requestDelete(note)}
                  onPreview={previewNote}
                  onPreviewEnd={endPreview}
                  onRename={() => startRename(note.path)}
                  onRenameDone={name => void finishRename(note.path, name)}
                  onSelect={() => selectNote(note.path)}
                  renaming={renamingPath === note.path}
                />
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
      return (
        <NoteMarkdown
          linkIndex={linkIndex}
          onOpenAnchor={anchor => openAnchorRef.current(anchor)}
          onOpenNote={path => openNoteRef.current(path)}
          text={draft}
        />
      )
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
        <NoteMarkdown
          linkIndex={linkIndex}
          onOpenAnchor={anchor => openAnchorRef.current(anchor)}
          onOpenNote={path => openNoteRef.current(path)}
          text={sourceText}
        />
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
    // 三栏各占一条轨道，两侧都能拖：左边的目录树、右边的大纲。
    // 用的是 app 通用的 PaneShell（pointer capture + 宽度持久化 + 双击复位），
    // 不是自己写的栅格——之前走 MasterDetail 的固定比例，宽度根本没法调。
    <PaneShell className="h-full min-h-0">
      <Pane id="notes-tree" minWidth={160} resizable side="left" width="15rem">
        <aside className="flex h-full min-h-0 flex-col p-2" data-testid="note-tree">
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
            {tree.folders.map(node => renderFolder(node, 0))}
            {tree.loose.map(note => (
              <NoteRow
                active={note.path === selected}
                key={note.path}
                note={note}
                onDelete={() => requestDelete(note)}
                onPreview={previewNote}
                onPreviewEnd={endPreview}
                onRename={() => startRename(note.path)}
                onRenameDone={name => void finishRename(note.path, name)}
                onSelect={() => selectNote(note.path)}
                renaming={renamingPath === note.path}
              />
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
          </div>
        </aside>
      </Pane>

      <PaneMain>
        <main className="flex h-full min-h-0 flex-col overflow-hidden">
          <NoteTabs active={selected} onClose={closeTab} onSelect={selectNote} tabs={tabs} />

          <div
            className={cn('min-h-0 flex-1 px-5 py-4 [scrollbar-gutter:stable]', editing && view === 'source' ? 'flex overflow-hidden' : 'overflow-y-auto overscroll-contain')}
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
      </PaneMain>

      <Pane id="notes-outline" minWidth={140} resizable side="right" width="13rem">
        <aside className="h-full overflow-y-auto px-2 py-3" data-testid="note-outline">
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
      </Pane>
    </PaneShell>
  )

  return (
    <PageSearchShell
      onSearchChange={setQuery}
      searchPlaceholder={v.searchPlaceholder}
      searchTrailingAction={
        <div className="flex items-center gap-1">
          <Button
            aria-label={v.quickSwitch}
            onClick={() => setSwitcherOpen(true)}
            size="icon-titlebar"
            title={v.quickSwitch}
            variant="ghost"
          >
            <Codicon name="search" size="0.85rem" />
          </Button>
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
        </div>
      }
      searchValue={query}
    >
      {body}

      {/* 快速切换器（mod+o）：输名字直接跳到一篇笔记 */}
      <NoteSwitcher
        notes={notes.map(note => ({ path: note.path, title: note.title, topic: note.topic }))}
        onClose={() => setSwitcherOpen(false)}
        onOpen={openFromSwitcher}
        open={switcherOpen}
      />

      {/* 删除笔记：二次确认。标题、说明、确认按钮都取既成的那一套文案。 */}
      <ConfirmDialog
        confirmLabel={t.fileMenu.delete}
        description={t.fileMenu.deleteBody}
        destructive
        onClose={() => setDeletingNote(null)}
        onConfirm={() => confirmDelete()}
        open={deletingNote !== null}
        title={deletingNote ? t.fileMenu.deleteTitle(deletingNote.title) : ''}
      />

      {/* 悬浮预览：停在目录树的一篇上，浮出正文开头；指针一走它就自己收掉。 */}
      {hoverNote && hoverPos && (
        <div className="fixed z-40" style={{ left: hoverPos.x, top: hoverPos.y }}>
          <HoverPreview
            active={rowHover}
            onClose={closePreview}
            onOpen={() => selectNote(hoverNote.path)}
            text={hoverNote.excerpt}
            title={hoverNote.title}
          />
        </div>
      )}
    </PageSearchShell>
  )
}
