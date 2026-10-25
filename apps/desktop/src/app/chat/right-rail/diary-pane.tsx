import { useQuery, useQueryClient } from '@tanstack/react-query'
import { EditorContent, useEditor } from '@tiptap/react'
import { Placeholder } from '@tiptap/extensions'
import StarterKit from '@tiptap/starter-kit'
import { marked } from 'marked'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { fetchDiaryEntry, fetchDiaryIndex, saveDiaryEntry } from './diary-api'
import { pmToMarkdown } from './markdown-serialize'

function todayISO(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

function shiftDay(day: string, delta: number): string {
  const [year, month, date] = day.split('-').map(Number)
  const shifted = new Date(year, month - 1, date + delta)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${shifted.getFullYear()}-${pad(shifted.getMonth() + 1)}-${pad(shifted.getDate())}`
}

function dayLabel(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  const when = new Date(year, month - 1, date)
  return when.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' })
}

function toHtml(markdown: string): string {
  const html = marked.parse(markdown || '', { async: false, gfm: true })
  return typeof html === 'string' ? html : ''
}

const EDITOR_CLASS = [
  'tiptap-diary min-h-[45vh] outline-none',
  'text-sm leading-7 text-foreground',
  '[&_h1]:mt-5 [&_h1]:mb-2 [&_h1]:text-xl [&_h1]:font-semibold',
  '[&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold',
  '[&_h3]:mt-4 [&_h3]:mb-1.5 [&_h3]:text-[0.95rem] [&_h3]:font-semibold',
  '[&_p]:my-1.5',
  '[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5',
  '[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5',
  '[&_li]:my-0.5',
  '[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-(--ui-stroke-secondary) [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
  '[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-(--ui-bg-quinary) [&_pre]:p-3 [&_pre]:font-mono [&_pre]:text-xs',
  '[&_code]:rounded [&_code]:bg-(--ui-bg-quinary) [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.82em]',
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0',
  '[&_hr]:my-5 [&_hr]:border-(--ui-stroke-tertiary)',
  '[&_strong]:font-semibold'
].join(' ')

function ToolbarButton({
  active,
  label,
  onClick,
  children
}: {
  active?: boolean
  label: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      aria-label={label}
      aria-pressed={active}
      className={cn(
        'grid h-6 min-w-6 place-items-center rounded px-1 font-mono text-[0.68rem] transition-colors',
        active ? 'bg-(--ui-control-hover-background) text-foreground' : 'text-muted-foreground hover:text-foreground'
      )}
      onClick={onClick}
      onMouseDown={event => event.preventDefault()}
      title={label}
      type="button"
    >
      {children}
    </button>
  )
}

function DiaryComposer({
  initialMarkdown,
  onChange,
  placeholder
}: {
  initialMarkdown: string
  onChange: (markdown: string) => void
  placeholder: string
}) {
  const { t } = useI18n()
  const d = t.diary
  const editor = useEditor({
    extensions: [StarterKit, Placeholder.configure({ placeholder })],
    content: toHtml(initialMarkdown),
    editorProps: { attributes: { class: EDITOR_CLASS, 'data-selectable-text': 'true' } },
    onUpdate: ({ editor: instance }) => onChange(pmToMarkdown(instance.getJSON()))
  })

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-(--ui-stroke-tertiary) px-2 py-1.5">
        <ToolbarButton
          active={editor?.isActive('bold')}
          label={d.bold}
          onClick={() => editor?.chain().focus().toggleBold().run()}
        >
          B
        </ToolbarButton>
        <ToolbarButton
          active={editor?.isActive('italic')}
          label={d.italic}
          onClick={() => editor?.chain().focus().toggleItalic().run()}
        >
          I
        </ToolbarButton>
        <ToolbarButton
          active={editor?.isActive('heading', { level: 2 })}
          label={d.heading}
          onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}
        >
          H2
        </ToolbarButton>
        <span className="mx-1 h-3.5 w-px bg-(--ui-stroke-tertiary)" />
        <ToolbarButton
          active={editor?.isActive('bulletList')}
          label={d.bulletList}
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
        >
          •
        </ToolbarButton>
        <ToolbarButton
          active={editor?.isActive('orderedList')}
          label={d.orderedList}
          onClick={() => editor?.chain().focus().toggleOrderedList().run()}
        >
          1.
        </ToolbarButton>
        <ToolbarButton
          active={editor?.isActive('blockquote')}
          label={d.quote}
          onClick={() => editor?.chain().focus().toggleBlockquote().run()}
        >
          ❝
        </ToolbarButton>
        <ToolbarButton
          active={editor?.isActive('codeBlock')}
          label={d.code}
          onClick={() => editor?.chain().focus().toggleCodeBlock().run()}
        >
          {'</>'}
        </ToolbarButton>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <EditorContent editor={editor} />
      </div>
    </div>
  )
}

export function DiaryPane() {
  const { t } = useI18n()
  const d = t.diary
  const queryClient = useQueryClient()
  const [day, setDay] = useState(todayISO)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const draftRef = useRef('')

  const index = useQuery({ queryKey: ['vault-diary'], queryFn: fetchDiaryIndex, staleTime: 30_000 })
  const entry = useQuery({
    queryKey: ['vault-diary', day],
    queryFn: () => fetchDiaryEntry(day),
    enabled: Boolean(day),
    staleTime: Infinity
  })

  const loadedText = entry.data?.text ?? ''
  useEffect(() => {
    if (entry.data === undefined) return
    setDraft(loadedText)
    draftRef.current = loadedText
    setDirty(false)
    setNotice('')
    setError('')
  }, [entry.data, loadedText])

  const save = async () => {
    if (saving) return
    setSaving(true)
    setError('')
    try {
      const result = await saveDiaryEntry(day, draftRef.current)
      setDirty(false)
      setNotice(result.committed ? d.savedWithCommit(result.commit) : d.unchanged)
      void queryClient.invalidateQueries({ queryKey: ['vault-diary'] })
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setSaving(false)
    }
  }

  const statusLine = useMemo(() => {
    if (error) return error
    if (saving) return d.saving
    if (dirty) return d.unsaved
    return notice || (entry.data?.exists ? d.saved : '')
  }, [d.saved, d.saving, d.unsaved, dirty, entry.data?.exists, error, notice, saving])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b border-(--ui-stroke-tertiary) px-2 py-1.5">
        <button
          aria-label={d.prevDay}
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-(--ui-control-hover-background) hover:text-foreground"
          onClick={() => setDay(current => shiftDay(current, -1))}
          title={d.prevDay}
          type="button"
        >
          ‹
        </button>
        <div className="min-w-0 flex-1 text-center">
          <div className="truncate text-[0.74rem] font-medium">{dayLabel(day)}</div>
          <div className="text-[0.6rem] tabular-nums text-muted-foreground/60">{day}</div>
        </div>
        <button
          aria-label={d.nextDay}
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-(--ui-control-hover-background) hover:text-foreground"
          onClick={() => setDay(current => shiftDay(current, 1))}
          title={d.nextDay}
          type="button"
        >
          ›
        </button>
        <Button
          className="h-6 px-1.5 text-[0.62rem]"
          onClick={() => setDay(todayISO())}
          size="xs"
          variant="ghost"
        >
          {d.today}
        </Button>
      </div>

      {entry.isError ? (
        <EmptyState
          description={entry.error instanceof Error ? entry.error.message : undefined}
          title={d.loadFailed}
        />
      ) : entry.isLoading || !entry.data ? (
        <PageLoader label={d.title} />
      ) : (
        <DiaryComposer
          initialMarkdown={loadedText}
          key={day}
          onChange={markdown => {
            draftRef.current = markdown
            setDraft(markdown)
            setDirty(true)
          }}
          placeholder={d.placeholder}
        />
      )}

      <div className="flex shrink-0 items-center gap-2 border-t border-(--ui-stroke-tertiary) px-2 py-1.5">
        <span className="min-w-0 flex-1 truncate text-[0.6rem] text-muted-foreground/60" title={statusLine}>
          {statusLine || d.chars(draft.length)}
        </span>
        {index.data && <span className="text-[0.6rem] tabular-nums text-muted-foreground/50">{index.data.entries.length}</span>}
        <Button disabled={!dirty || saving} onClick={() => void save()} size="xs">
          {saving ? d.saving : d.save}
        </Button>
      </div>
    </div>
  )
}

