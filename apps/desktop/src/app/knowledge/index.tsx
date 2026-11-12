import { useQuery } from '@tanstack/react-query'
import { type ReactNode, type UIEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { PageSearchShell } from '@/app/page-search-shell'
import { PageLoader } from '@/components/page-loader'
import { Pane, PaneMain, PaneShell } from '@/components/pane-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from '@/components/ui/context-menu'
import { EmptyState } from '@/components/ui/empty-state'
import { translateNow, useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'

import { deleteNote, fetchNote, fetchNoteLinks, fetchNotes, NOTES_PREFIX, renameNote, saveNote } from './api'
import type { NoteMeta } from './api'
import { readBookmarks, toggleBookmark } from './bookmarks'
import { NoteEditor } from './editor'
import { HoverPreview } from './hover-preview'
import { jumpToAnchor, makeLinkIndex, NoteMarkdown } from './note-markdown'
import { noteBaseDir } from './note-media'
import { buildNoteTree, noteTemplate, parseOutline } from './outline'
import type { NoteTreeNode } from './outline'
import { NotePanels } from './panels'
import { NoteSwitcher, useNoteSwitcherEscape, useNoteSwitcherHotkey } from './switcher'
import { NoteTabs } from './tabs'

function matches(note: NoteMeta, needle: string, tag: string | null): boolean {
  if (tag && !(note.tags ?? []).includes(tag)) return false
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
  /** 右栏「标签」面板点一下 ⇒ 左栏按这个标签筛。 */
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  /** 书签（落在本机，不进笔记仓）。 */
  const [bookmarks, setBookmarks] = useState<string[]>([])

  const [editing, setEditing] = useState(false)
  const [view, setView] = useState<'source' | 'preview'>('source')
  const [draft, setDraft] = useState('')
  const [base, setBase] = useState('')
  const [isNew, setIsNew] = useState(false)
  const [saving, setSaving] = useState(false)
  /** 保存的同步闸：`saving` 是 state，两下 `Ctrl+S` 会一起穿过去。 */
  const savingRef = useRef(false)
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
  /** 全库的链接图（反向链接面板要它）：一次给全，不用前端一篇篇拉正文。 */
  const graph = useQuery({ queryKey: ['surface-note-links'], queryFn: fetchNoteLinks, staleTime: 0 })
  const page = useQuery({
    queryKey: ['surface-note', selected],
    queryFn: () => fetchNote(selected as string),
    enabled: Boolean(selected) && !isNew,
    staleTime: 0
  })

  // 书签：第一次进来时从本机读一份（落在 localStorage，不进笔记仓）。
  useEffect(() => {
    setBookmarks(readBookmarks())
  }, [])

  function toggleCurrentBookmark() {
    if (!selected) return
    setBookmarks(toggleBookmark(selected))
  }

  const visible = useMemo(() => notes.filter(note => matches(note, query.trim(), tagFilter)), [notes, query, tagFilter])
  const tree = useMemo(() => buildNoteTree(visible), [visible])
  const searching = query.trim().length > 0

  // wikilink 解析用的索引：库里有哪些笔记（按文件名与标题两套键）。
  const linkIndex = useMemo(() => makeLinkIndex(notes), [notes])

  useEffect(() => {
    // 改名/删除还在路上：选中哪一格由我们自己定。
    if (pendingPath) {
      return
    }

    // 按标签筛的时候**不换当前读的那篇**（Obsidian 筛文件列表时不带走正在读的
    // 笔记）；否则一点标签，正文就跳到别的篇去了。
    if (tagFilter) {
      return
    }

    if (isNew || (selected && visible.some(note => note.path === selected))) {
      return
    }

    const first = visible[0]?.path ?? null
    setSelected(first)
    // 初始那一格也开成标签——Obsidian 的手感：打开过的笔记就留在标签栏上。
    if (first) setOpenTabs(previous => (previous.includes(first) ? previous : [...previous, first]))
  }, [isNew, pendingPath, selected, tagFilter, visible])

  const activeNote = notes.find(note => note.path === selected) ?? null
  const sourceText = editing ? draft : (page.data?.text ?? '')
  const outline = useMemo(() => parseOutline(sourceText), [sourceText])
  const dirty = editing && draft !== base

  // 图片：相对路径按「笔记同目录 → 同目录的 attachments/ → 库根」找盘上那一份。
  // 库根来自清单（`NotesLibrary.root`），笔记目录来自这一篇自己的 `fsPath`。
  const assetRoot = library.data?.root
  const baseDir = useMemo(() => noteBaseDir(page.data?.fsPath), [page.data?.fsPath])

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

  /**
   * 给正文字典的稳定回调。
   *
   * 别写内联箭头：`NoteMarkdown` 的 `components` 是 `useMemo([linkIndex,
   * onOpenAnchor, onOpenNote])`，内联箭头每次渲染都是新地址，`useMemo`
   * 每次都失效，于是整棵 Markdown 的组件类型全换新，正文 DOM 卸载重挂。
   * 地址按住不动，正文才可能被 `memo` 挡住——滚动只动大纲，不动正文。
   */
  const handleOpenAnchor = useCallback((anchor: string) => openAnchorRef.current(anchor), [])
  const handleOpenNote = useCallback((path: string) => openNoteRef.current(path), [])

  // 标题的「内容坐标」缓存：滚动时只查这张表，不再挨个碰布局。
  const headingTopsRef = useRef<{ id: string; top: number }[]>([])
  const scrollTopRef = useRef(0)
  const scrollFrameRef = useRef(0)

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
    // ⚠️ 两个入口会同时按保存：编辑器自己的 `Ctrl+S` 键位，**和**窗口那一层的
    // 全局 `Ctrl+S`（焦点在右栏大纲时也要能存，Obsidian 就是全局的）。
    // `saving` 是 state，异步更新 ⇒ 两下会一起穿过去 ⇒ 这里用一个**同步**的闸。
    if (!selected || savingRef.current) {
      return
    }
    savingRef.current = true
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
      savingRef.current = false
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

  /**
   * 滚动时定「当前读到哪个标题」。
   *
   * 老写法是每个 scroll 事件把 `outline` 全过一遍，每个标题一次
   * `getBoundingClientRect()`——一篇两百 KB 的笔记有近两百个标题、
   * 十几万 DOM 节点，实测每帧 45～70ms，掉到 15fps，长任务堆成山
   * （5 秒滚动里 55 个长任务、累计 4.4s）。滚动是高频事件，不能干
   * 这种活。
   *
   * 现在：滚动只记 `scrollTop`，被 `requestAnimationFrame` 合成成一帧
   * 一次；标题位置来自 `headingTopsRef` 里预先算好的内容坐标，查表
   * 用二分，全程不碰布局。
   */
  function onScroll(event: UIEvent<HTMLDivElement>) {
    scrollTopRef.current = event.currentTarget.scrollTop

    if (scrollFrameRef.current) {return}

    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = 0
      const list = headingTopsRef.current
      const line = scrollTopRef.current + 24
      let current: string | null = null
      let low = 0
      let high = list.length - 1

      while (low <= high) {
        const middle = (low + high) >> 1

        if (list[middle].top <= line) {
          current = list[middle].id
          low = middle + 1
        } else {
          high = middle - 1
        }
      }

      setActiveHeading(previous => (previous === current ? previous : current))
    })
  }

  /**
   * 重建标题的内容坐标表。
   *
   * 只在「换篇／正文变了／容器或正文尺寸变了」时算一次：这时候量
   * 布局是应当付的钱，滚动手上就只剩查表。用 `ResizeObserver` 而不
   * 是滚动时重算，是因为真正会挪动标题的只有图片落地、窗口变宽、
   * 正文换掉这几件事。
   */
  useEffect(() => {
    const container = scrollRef.current

    if (!container) {return}

    const rebuild = () => {
      // 容器内容原点的视口坐标：滚到哪都不影响它，减掉就能换算成内容坐标。
      const origin = container.getBoundingClientRect().top - container.scrollTop
      const list: { id: string; top: number }[] = []

      for (const item of outline) {
        const element = document.getElementById(item.id)

        if (!element) {continue}
        list.push({ id: item.id, top: element.getBoundingClientRect().top - origin })
      }

      // 二分查表要求按位置升序：文档顺序一般就是视觉顺序，但不假定它。
      list.sort((left, right) => left.top - right.top)
      headingTopsRef.current = list
    }

    rebuild()

    // jsdom 里没有 ResizeObserver：拿不到就只在挂载时量一次，测试环境不至于炸。
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(rebuild)

    if (observer) {
      observer.observe(container)

      if (container.firstElementChild) {observer.observe(container.firstElementChild)}
    }

    return () => {
      observer?.disconnect()
      headingTopsRef.current = []
    }
  }, [outline, selected, editing, view])

  // 卸载时把还挂着的这一帧掐掉，别让回调打到已经下来的组件上。
  useEffect(() => () => {
    if (scrollFrameRef.current) {window.cancelAnimationFrame(scrollFrameRef.current)}
  }, [])

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
        <NoteEditor
          initialValue={draft}
          key={selected}
          label={v.source}
          onChange={setDraft}
          onSave={() => void save()}
        />
      )
    }
    if (editing && view === 'preview') {
      return (
        <NoteMarkdown
          assetRoot={assetRoot}
          baseDir={baseDir}
          linkIndex={linkIndex}
          onOpenAnchor={handleOpenAnchor}
          onOpenNote={handleOpenNote}
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
          <div className="flex items-start gap-2">
            <h1 className="flex-1 text-lg font-semibold leading-tight">{activeNote?.title ?? ''}</h1>
            {selected && (
              <button
                aria-label={bookmarks.includes(selected) ? v.unbookmark : v.bookmark}
                aria-pressed={bookmarks.includes(selected)}
                className={cn(
                  'shrink-0 rounded px-1 text-[0.8rem] leading-none transition-colors',
                  bookmarks.includes(selected)
                    ? 'text-(--ui-text-tertiary) hover:text-foreground'
                    : 'text-(--ui-text-tertiary)/50 hover:text-(--ui-text-secondary)'
                )}
                onClick={toggleCurrentBookmark}
                type="button"
              >
                {bookmarks.includes(selected) ? '★' : '☆'}
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[0.66rem] text-muted-foreground/70">
            {activeNote && <Badge variant="outline">{activeNote.topic}</Badge>}
            {activeNote?.tags.slice(0, 4).map(tag => <span key={tag}>#{tag}</span>)}
            <span className="ml-auto tabular-nums">
              {v.updated} {activeNote?.updated || '—'}
            </span>
          </div>
        </header>
        <NoteMarkdown
          assetRoot={assetRoot}
          baseDir={baseDir}
          linkIndex={linkIndex}
          onOpenAnchor={handleOpenAnchor}
          onOpenNote={handleOpenNote}
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
          <NotePanels
            bookmarks={bookmarks}
            current={activeNote ? { path: activeNote.path, title: activeNote.title } : null}
            graph={graph.data ?? null}
            linkIndex={linkIndex}
            notes={notes}
            onFilterTag={tag => setTagFilter(current => (current === tag ? null : tag))}
            onOpenNote={handleOpenNote}
            onToggleBookmark={path => setBookmarks(toggleBookmark(path))}
            text={sourceText}
          />
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
