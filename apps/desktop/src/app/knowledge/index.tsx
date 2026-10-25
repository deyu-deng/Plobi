import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { DetailColumn, ListColumn, MasterDetail } from '@/app/master-detail'
import { PageSearchShell, type PageShellTab } from '@/app/page-search-shell'
import { CompactMarkdown } from '@/components/chat/compact-markdown'
import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { fetchNote, fetchNotes } from './api'
import type { NoteMeta } from './api'

function stripFrontmatter(text: string): string {
  if (!text.startsWith('---')) return text
  const end = text.indexOf('\n---', 3)
  return end < 0 ? text : text.slice(end + 4).replace(/^\s*\n/, '')
}

function matches(note: NoteMeta, needle: string): boolean {
  if (!needle) return true
  const hay = [note.title, note.excerpt, note.topic, ...note.tags].join('\n').toLowerCase()
  return hay.includes(needle.toLowerCase())
}

function NoteRow({ active, note, onSelect }: { active: boolean; note: NoteMeta; onSelect: () => void }) {
  return (
    <button
      className={cn(
        'flex w-full flex-col gap-1 rounded-md px-2.5 py-2 text-left transition-colors',
        'hover:bg-(--ui-control-hover-background)/60',
        active && 'bg-(--ui-control-hover-background)'
      )}
      onClick={onSelect}
      type="button"
    >
      <div className="flex items-center gap-2">
        <span className="min-w-0 truncate text-[0.8rem] font-medium">{note.title}</span>
        <span className="ml-auto shrink-0 text-[0.62rem] tabular-nums text-muted-foreground/60">
          {note.updated || '—'}
        </span>
      </div>
      {note.excerpt && (
        <span className="line-clamp-2 text-[0.68rem] leading-snug text-muted-foreground/75">{note.excerpt}</span>
      )}
      <div className="flex flex-wrap items-center gap-1">
        <Badge variant="outline">{note.topic}</Badge>
        {note.tags.slice(0, 3).map(tag => (
          <span className="text-[0.6rem] text-muted-foreground/60" key={tag}>
            #{tag}
          </span>
        ))}
      </div>
    </button>
  )
}

export function KnowledgeView() {
  const { t } = useI18n()
  const v = t.vault
  const [topic, setTopic] = useState('all')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)

  const library = useQuery({ queryKey: ['vault-notes'], queryFn: fetchNotes, staleTime: 0 })
  const notes = library.data?.notes ?? []
  const topics = library.data?.topics ?? []

  const tabs = useMemo<PageShellTab[]>(
    () => [
      { id: 'all', label: v.all, meta: notes.length },
      ...topics.map(group => ({ id: group.name, label: group.name, meta: group.count }))
    ],
    [notes.length, topics, v.all]
  )

  const visible = useMemo(
    () =>
      notes.filter(
        note => (topic === 'all' || note.topic === topic) && matches(note, query.trim())
      ),
    [notes, query, topic]
  )

  useEffect(() => {
    if (selected && visible.some(note => note.path === selected)) return
    setSelected(visible[0]?.path ?? null)
  }, [selected, visible])

  const page = useQuery({
    queryKey: ['vault-note', selected],
    queryFn: () => fetchNote(selected as string),
    enabled: Boolean(selected),
    staleTime: 0
  })

  const activeNote = visible.find(note => note.path === selected) ?? null

  const body = library.isError ? (
    <EmptyState
      description={library.error instanceof Error ? library.error.message : undefined}
      title={v.loadFailed}
    />
  ) : library.isLoading ? (
    <PageLoader label={v.loading} />
  ) : visible.length === 0 ? (
    <EmptyState description={notes.length ? v.noMatchDesc : v.emptyDesc} title={notes.length ? v.noMatchTitle : v.emptyTitle} />
  ) : (
    <MasterDetail split="wide">
      <ListColumn>
        <div className="flex h-full flex-col gap-0.5">
          {visible.map(note => (
            <NoteRow
              active={note.path === selected}
              key={note.path}
              note={note}
              onSelect={() => setSelected(note.path)}
            />
          ))}
        </div>
      </ListColumn>
      <DetailColumn footer={v.readOnly}>
        {!activeNote ? (
          <EmptyState title={v.emptyTitle} />
        ) : (
          <article className="space-y-3">
            <header className="space-y-2">
              <h1 className="text-lg font-semibold leading-tight">{activeNote.title}</h1>
              <div className="flex flex-wrap items-center gap-1.5 text-[0.66rem] text-muted-foreground/70">
                <Badge variant="outline">{activeNote.topic}</Badge>
                {activeNote.tags.map(tag => (
                  <span key={tag}>#{tag}</span>
                ))}
                <span className="ml-auto tabular-nums">
                  {v.updated} {activeNote.updated || '—'}
                </span>
              </div>
              <p className="truncate font-mono text-[0.62rem] text-muted-foreground/50">{activeNote.path}</p>
            </header>

            {page.isError ? (
              <EmptyState
                description={page.error instanceof Error ? page.error.message : undefined}
                title={v.loadFailed}
              />
            ) : page.isLoading ? (
              <PageLoader label={v.loading} />
            ) : (
              <div className="rounded-lg border border-(--ui-stroke-tertiary)">
                <CompactMarkdown
                  className="px-3 py-3 text-sm leading-relaxed text-foreground"
                  text={stripFrontmatter(page.data?.text ?? '')}
                />
              </div>
            )}
          </article>
        )}
      </DetailColumn>
    </MasterDetail>
  )

  return (
    <PageSearchShell
      activeTab={topic}
      onSearchChange={setQuery}
      onTabChange={setTopic}
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
      tabs={tabs}
    >
      {body}
    </PageSearchShell>
  )
}
