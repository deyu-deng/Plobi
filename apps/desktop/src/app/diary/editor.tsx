import { Placeholder } from '@tiptap/extensions'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { marked } from 'marked'
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'

import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { saveDiaryEntry } from './api'
import { pmToMarkdown } from './markdown-serialize'

function toHtml(markdown: string): string {
  const html = marked.parse(markdown || '', { async: false, gfm: true })
  return typeof html === 'string' ? html : ''
}

const EDITOR_CLASS = [
  'min-h-[45vh] outline-none',
  'text-[0.9rem] leading-8 text-foreground',
  '[&_h1]:mt-6 [&_h1]:mb-3 [&_h1]:text-xl [&_h1]:font-semibold',
  '[&_h2]:mt-6 [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold',
  '[&_h3]:mt-4 [&_h3]:mb-1.5 [&_h3]:text-[0.95rem] [&_h3]:font-semibold',
  '[&_p]:my-2',
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

function ToolButton({
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

export function DiaryEditor({
  baseText,
  date,
  exists,
  onSaved
}: {
  baseText: string
  date: string
  exists: boolean
  onSaved: (message: string) => void
}) {
  const { t } = useI18n()
  const d = t.diary
  const draftRef = useRef(baseText)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const editor = useEditor({
    extensions: [StarterKit, Placeholder.configure({ placeholder: d.placeholder })],
    content: toHtml(baseText),
    editorProps: { attributes: { class: EDITOR_CLASS, 'data-selectable-text': 'true' } },
    onUpdate: ({ editor: instance }) => {
      draftRef.current = pmToMarkdown(instance.getJSON())
      setDirty(draftRef.current !== baseText)
    }
  })

  const save = useCallback(async () => {
    if (saving) return
    setSaving(true)
    setError('')
    try {
      // 带上打开时那份原文：中间用浮层记的那几句会被合并回来，而不是被整篇覆盖掉。
      const result = await saveDiaryEntry(date, draftRef.current, baseText)
      const message = result.committed
        ? result.merged > 0
          ? d.mergedInto(d.merged(result.merged), d.savedWithCommit(result.commit))
          : d.savedWithCommit(result.commit)
        : d.unchanged
      setNotice(message)
      onSaved(message)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setSaving(false)
    }
  }, [baseText, d.merged, d.savedWithCommit, d.unchanged, date, onSaved, saving])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (dirty) void save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dirty, save])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-(--ui-stroke-tertiary) px-3 py-1.5">
        <ToolButton active={editor?.isActive('bold')} label={d.bold} onClick={() => editor?.chain().focus().toggleBold().run()}>
          B
        </ToolButton>
        <ToolButton
          active={editor?.isActive('italic')}
          label={d.italic}
          onClick={() => editor?.chain().focus().toggleItalic().run()}
        >
          I
        </ToolButton>
        <ToolButton
          active={editor?.isActive('heading', { level: 2 })}
          label={d.heading}
          onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}
        >
          H2
        </ToolButton>
        <span className="mx-1 h-3.5 w-px bg-(--ui-stroke-tertiary)" />
        <ToolButton
          active={editor?.isActive('bulletList')}
          label={d.bulletList}
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
        >
          •
        </ToolButton>
        <ToolButton
          active={editor?.isActive('orderedList')}
          label={d.orderedList}
          onClick={() => editor?.chain().focus().toggleOrderedList().run()}
        >
          1.
        </ToolButton>
        <ToolButton
          active={editor?.isActive('blockquote')}
          label={d.quote}
          onClick={() => editor?.chain().focus().toggleBlockquote().run()}
        >
          ❝
        </ToolButton>
        <ToolButton
          active={editor?.isActive('codeBlock')}
          label={d.code}
          onClick={() => editor?.chain().focus().toggleCodeBlock().run()}
        >
          {'</>'}
        </ToolButton>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-2xl">
          <EditorContent editor={editor} />
          {!exists && !dirty && (
            <div className="pointer-events-none -mt-8 text-sm text-muted-foreground/50">{d.placeholder}</div>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2 border-t border-(--ui-stroke-tertiary) px-3 py-1.5">
        <span className="min-w-0 flex-1 truncate text-[0.66rem] text-muted-foreground/70" title={error || notice}>
          {error || notice || (dirty ? d.unsaved : exists ? d.saved : '')}
        </span>
        <span className="shrink-0 text-[0.62rem] tabular-nums text-muted-foreground/50">
          {d.chars(draftRef.current.length)}
        </span>
        <button
          className="rounded-md bg-primary px-2.5 py-1 text-[0.7rem] font-medium text-primary-foreground disabled:opacity-45"
          disabled={!dirty || saving}
          onClick={() => void save()}
          type="button"
        >
          {saving ? d.saving : d.save}
        </button>
      </div>
    </div>
  )
}

export function DiaryLoadFailed({ message }: { message: string }) {
  const { t } = useI18n()
  return <EmptyState description={message} title={t.diary.loadFailed} />
}
