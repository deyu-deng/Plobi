import { useMemo, useState } from 'react'

import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import type { NoteLinkGraph, NoteMeta } from './api'
import { parseFrontmatter } from './frontmatter'
import { findWikiLinks, type NoteLinkIndex, resolveWikiNote } from './wikilink'

/**
 * 笔记栏右栏的那一叠面板（Obsidian 的右栏工作区）。
 *
 * 五块：反向链接 / 出链 / 标签 / 属性 / 书签。每块可折叠，折叠状态是**本机**的
 * （localStorage）—— Obsidian 也是这样：右栏的形状是每个工作区自己的一份。
 *
 * 屏上纪律（用户 10-27 令）：只写「这是什么」，不写机制；状态用形状表达；
 * 空的那块就**空着**（一行破折号），不写「暂无…」这类安慰话。
 */

/** 右栏的一段：标题 ＋ 计数 ＋ 可折叠的内容。 */
function Section({
  children,
  count,
  defaultOpen = true,
  testId,
  title
}: {
  children: React.ReactNode
  count?: number
  defaultOpen?: boolean
  testId: string
  title: string
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <section className="mb-3" data-testid={testId}>
      <button
        aria-expanded={open}
        className="flex w-full items-center gap-1 py-1 text-left text-[0.62rem] font-medium text-(--ui-text-secondary) transition-colors hover:text-foreground"
        onClick={() => setOpen(value => !value)}
        type="button"
      >
        <span className="w-2 shrink-0 text-[0.6rem] text-muted-foreground/60">{open ? '▾' : '▸'}</span>
        <span className="truncate">{title}</span>
        {typeof count === 'number' && count > 0 && <span className="ml-auto tabular-nums">{count}</span>}
      </button>
      {open && <div className="mt-0.5 space-y-0.5 pl-3">{children}</div>}
    </section>
  )
}

/** 一行笔记（出链／反向链接／书签共用一个形状）。 */
function NoteRow({
  broken,
  label,
  onClick,
  title
}: {
  broken?: boolean
  label: string
  onClick?: () => void
  title?: string
}) {
  return (
    <button
      className={cn(
        'block w-full truncate rounded px-1 py-0.5 text-left text-[0.66rem] transition-colors hover:bg-(--ui-control-hover-background)/60',
        broken ? 'text-(--ui-text-tertiary)' : 'text-(--ui-text-secondary) hover:text-foreground'
      )}
      onClick={onClick}
      title={title ?? label}
      type="button"
    >
      {label}
    </button>
  )
}

export interface NotePanelsProps {
  /** 书签列表（逻辑路径）。 */
  bookmarks: string[]
  /** 当前笔记的正文（出链与属性从这里算）。 */
  text: string
  /** 全库的链接图（反向链接要它）。 */
  graph: NoteLinkGraph | null
  /** 库里有哪些笔记（把路径显示成标题、把标签汇总出来）。 */
  notes: NoteMeta[]
  /** 当前笔记（没选中时是 null）。 */
  current: { path: string; title: string } | null
  /** 标题／文件名 → 笔记（出链解析用）。 */
  linkIndex: NoteLinkIndex
  /** 点一个标签 ⇒ 左栏按它筛。 */
  onFilterTag: (tag: string) => void
  /** 打开另一篇笔记。 */
  onOpenNote: (path: string) => void
  /** 书签加减。 */
  onToggleBookmark: (path: string) => void
}

export function NotePanels({
  bookmarks,
  current,
  graph,
  linkIndex,
  notes,
  onFilterTag,
  onOpenNote,
  onToggleBookmark,
  text
}: NotePanelsProps) {
  const { t } = useI18n()
  const v = t.surface

  const titleOf = useMemo(() => {
    const map = new Map<string, string>()

    for (const note of notes) {
      map.set(note.path, note.title)
    }
    if (graph) {
      for (const [path, title] of Object.entries(graph.titles)) {
        if (!map.has(path)) map.set(path, title)
      }
    }

    return map
  }, [graph, notes])

  /** 出链：正文里的 wikilink（解析不上的原样留着，屏上标成「未创建」）。 */
  const outgoing = useMemo(() => {
    if (!current) return []

    const seen = new Set<string>()
    const out: { broken: boolean; label: string; path: string }[] = []

    for (const link of findWikiLinks(text)) {
      if (link.kind !== 'note') continue

      const target = resolveWikiNote(link, linkIndex)

      if (target) {
        if (seen.has(target.path)) continue
        seen.add(target.path)
        out.push({ broken: false, label: target.title, path: target.path })
        continue
      }

      if (seen.has(link.target)) continue
      seen.add(link.target)
      out.push({ broken: true, label: link.target, path: '' })
    }

    return out
  }, [current, linkIndex, text])

  /** 反向链接：谁链到了这一篇。 */
  const backlinks = useMemo(() => {
    if (!current || !graph) return []

    return (graph.backlinks[current.path] ?? []).map(path => ({
      label: titleOf.get(path) ?? path.split('/').pop() ?? path,
      path
    }))
  }, [current, graph, titleOf])

  /** 标签：库里全部的标签（带篇数），当前这篇的标签标出来。 */
  const tags = useMemo(() => {
    const counts = new Map<string, number>()

    for (const note of notes) {
      for (const tag of note.tags ?? []) {
        counts.set(tag, (counts.get(tag) ?? 0) + 1)
      }
    }

    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  }, [notes])

  const currentTags = useMemo(() => {
    const note = notes.find(item => item.path === current?.path)

    return new Set(note?.tags ?? [])
  }, [current, notes])

  /** 属性：这一篇的 frontmatter。 */
  const properties = useMemo(() => parseFrontmatter(text), [text])

  return (
    <div data-testid="note-panels">
      <Section count={backlinks.length} testId="note-backlinks" title={v.backlinks}>
        {backlinks.length === 0 ? (
          <span className="text-[0.62rem] text-(--ui-text-tertiary)">—</span>
        ) : (
          backlinks.map(item => (
            <NoteRow key={item.path} label={item.label} onClick={() => onOpenNote(item.path)} title={item.path} />
          ))
        )}
      </Section>

      <Section count={outgoing.length} testId="note-outgoing" title={v.outgoingLinks}>
        {outgoing.length === 0 ? (
          <span className="text-[0.62rem] text-(--ui-text-tertiary)">—</span>
        ) : (
          outgoing.map(item => (
            <NoteRow
              broken={item.broken}
              key={item.path || item.label}
              label={item.label}
              onClick={item.broken ? undefined : () => onOpenNote(item.path)}
            />
          ))
        )}
      </Section>

      <Section count={tags.length} testId="note-tags" title={v.tags}>
        {tags.length === 0 ? (
          <span className="text-[0.62rem] text-(--ui-text-tertiary)">—</span>
        ) : (
          tags.map(([tag, count]) => (
            <button
              className={cn(
                'flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-[0.66rem] transition-colors hover:bg-(--ui-control-hover-background)/60',
                currentTags.has(tag) ? 'text-foreground' : 'text-(--ui-text-secondary)'
              )}
              key={tag}
              onClick={() => onFilterTag(tag)}
              type="button"
            >
              <span className="truncate">#{tag}</span>
              <span className="ml-auto tabular-nums text-[0.6rem] text-muted-foreground/60">{count}</span>
            </button>
          ))
        )}
      </Section>

      <Section count={properties.length} testId="note-properties" title={v.properties}>
        {properties.length === 0 ? (
          <span className="text-[0.62rem] text-(--ui-text-tertiary)">—</span>
        ) : (
          properties.map(entry => (
            <div className="flex gap-2 px-1 py-0.5 text-[0.66rem]" key={entry.key}>
              <span className="shrink-0 text-(--ui-text-tertiary)">{entry.key}</span>
              <span className="min-w-0 flex-1 truncate text-(--ui-text-secondary)" title={entry.value}>
                {entry.value}
              </span>
            </div>
          ))
        )}
      </Section>

      <Section count={bookmarks.length} testId="note-bookmarks" title={v.bookmarks}>
        {bookmarks.length === 0 ? (
          <span className="text-[0.62rem] text-(--ui-text-tertiary)">—</span>
        ) : (
          bookmarks.map(path => (
            <div className="flex items-center gap-1" key={path}>
              <NoteRow label={titleOf.get(path) ?? path.split('/').pop() ?? path} onClick={() => onOpenNote(path)} title={path} />
              <button
                aria-label={v.unbookmark}
                className="shrink-0 px-1 text-[0.62rem] text-(--ui-text-tertiary) transition-colors hover:text-foreground"
                onClick={() => onToggleBookmark(path)}
                type="button"
              >
                ×
              </button>
            </div>
          ))
        )}
      </Section>
    </div>
  )
}
