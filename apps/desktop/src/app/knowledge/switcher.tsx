import { useEffect, useMemo, useRef, useState } from 'react'

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

/**
 * 笔记快速切换器（`mod+o`）。
 *
 * 为什么不挂在 `mod+k`：`mod+k` 是**全局命令面板**的既有绑定
 * （`nav.commandPalette`，默认 `mod+k` + `mod+p`）。抢它等于拆用户已有的
 * 命令面板，所以这里另起 `mod+o`——这也是 Obsidian 的「快速打开」键。
 *
 * 匹配规则：标题、文件名、学科目录都参与，大小写不敏感；中文按子串匹配，
 * 英文再补一个「首字母缩写」匹配（`wx` → 微积分）。
 */

export interface SwitcherNote {
  path: string
  title: string
  /** 学科 / 子类，用于分组与副标题。 */
  topic?: string
}

export interface NoteSwitcherProps {
  notes: SwitcherNote[]
  onOpen: (path: string) => void
  onClose: () => void
  open: boolean
}

function score(note: SwitcherNote, needle: string): number {
  if (!needle) return 1
  const lower = needle.toLowerCase()
  const title = note.title.toLowerCase()
  const file = (note.path.split('/').pop() ?? '').replace(/\.md$/i, '').toLowerCase()
  const topic = (note.topic ?? '').toLowerCase()

  if (title === lower || file === lower) return 100
  if (title.startsWith(lower) || file.startsWith(lower)) return 80
  if (title.includes(lower) || file.includes(lower)) return 60
  if (topic.includes(lower)) return 30

  // 首字母缩写：微积分 → wx
  const initials = [...title].filter(char => /[\p{L}\p{N}]/u.test(char)).map(char => char.toLowerCase())
  let acronym = ''
  for (const char of initials) {
    acronym += char
    if (acronym === lower) return 50
  }

  return 0
}

export function NoteSwitcher({ notes, onClose, onOpen, open }: NoteSwitcherProps) {
  const { t } = useI18n()
  const v = t.surface
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (open) setQuery('')
  }, [open])

  const results = useMemo(() => {
    const needle = query.trim()
    return notes
      .map(note => ({ note, weight: score(note, needle) }))
      .filter(entry => entry.weight > 0)
      .sort((a, b) => b.weight - a.weight || a.note.title.localeCompare(b.note.title, 'zh-Hans-CN'))
      .slice(0, 40)
  }, [notes, query])

  if (!open) return null

  const groups = new Map<string, SwitcherNote[]>()
  for (const entry of results) {
    const key = entry.note.topic ?? ''
    const bucket = groups.get(key)
    if (bucket) bucket.push(entry.note)
    else groups.set(key, [entry.note])
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/20 pt-[12vh]"
      data-testid="note-switcher"
      onClick={event => {
        if (event.target === event.currentTarget) onClose()
      }}
      role="presentation"
    >
      <Command
        className="w-[min(34rem,90vw)] overflow-hidden rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-chat-surface-background) shadow-lg"
        onValueChange={() => {
          /* 结果由 query 自己算，cmdk 的过滤关掉。 */
        }}
        shouldFilter={false}
      >
        <CommandInput onValueChange={setQuery} placeholder={v.quickSwitch} value={query} />
        <CommandList>
          <CommandEmpty>{v.noMatchTitle}</CommandEmpty>
          {[...groups.entries()].map(([topic, items]) => (
            <CommandGroup heading={topic || undefined} key={topic}>
              {items.map(note => (
                <CommandItem
                  key={note.path}
                  onSelect={() => {
                    onOpen(note.path)
                    onClose()
                  }}
                  value={note.path}
                >
                  <span className="truncate">{note.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
        </CommandList>
      </Command>
    </div>
  )
}

/** 快速切换器的键盘入口：`mod+o`。 */
export function useNoteSwitcherHotkey(open: boolean, onToggle: (next: boolean) => void): void {
  const toggleRef = useRef(onToggle)
  toggleRef.current = onToggle

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return
      if (event.key.toLowerCase() !== 'o') return
      event.preventDefault()
      toggleRef.current(!open)
    }
    window.addEventListener('keydown', handler, { capture: true })
    return () => window.removeEventListener('keydown', handler, { capture: true })
  }, [open])
}

/** 切换器打开时，Esc 关掉它。 */
export function useNoteSwitcherEscape(open: boolean, onClose: () => void): void {
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open) return
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closeRef.current()
      }
    }
    window.addEventListener('keydown', handler, { capture: true })
    return () => window.removeEventListener('keydown', handler, { capture: true })
  }, [open])
}

export const switcherOverlayClass = cn('fixed inset-0 z-50')
