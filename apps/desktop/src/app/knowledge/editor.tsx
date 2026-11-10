import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { markdown, markdownKeymap } from '@codemirror/lang-markdown'
import { bracketMatching } from '@codemirror/language'
import { openSearchPanel, search, searchKeymap } from '@codemirror/search'
import { Compartment, EditorSelection, EditorState, type Extension } from '@codemirror/state'
import { drawSelection, EditorView, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers } from '@codemirror/view'
import { useEffect, useMemo, useRef, useState } from 'react'

import { githubEditorTheme } from '@/components/chat/code-editor-theme'
import { cn } from '@/lib/utils'
import { useTheme } from '@/themes/context'

/**
 * 笔记的 Markdown 编辑器（CodeMirror 6）。
 *
 * 为什么换掉那个裸 `<Textarea>`：Obsidian 的编辑面是 CodeMirror，用户要的是
 * **能正经写长笔记**的那套手感——语法高亮、行号、查找替换、多光标、状态行。
 * 一个 textarea 给不了这些，而长文（最长那篇 10 万字、272 个标题）正是这屏
 * 的主场景。
 *
 * 复用既有件，不另起一套：
 *   - 配色走 `components/chat/code-editor-theme.ts`（与阅读态的 Shiki 主题同源，
 *     来回切不换色）；
 *   - 查找替换走官方 `@codemirror/search`（Obsidian 的 Ctrl+F 同款面板）。
 *
 * 快捷键（与 Obsidian 对齐）：
 *   `Ctrl+S` 保存 · `Ctrl+F` 查找替换 · `Ctrl+Alt+↑/↓` 在上/下一行加光标 ·
 *   `Ctrl+Z` / `Ctrl+Shift+Z` 撤销重做 · `Alt+↑/↓` 移动行
 */

/** 字数：中文按字算，英文按词算（Obsidian 就是这么数的）。 */
export function countWords(text: string): number {
  const cjk = text.match(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/g)?.length ?? 0
  const latin = text.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g)?.length ?? 0

  return cjk + latin
}

/**
 * 在上/下一行加一个光标（Obsidian 的 `Ctrl+Alt+↑/↓`）。
 *
 * CodeMirror 6 没有自带这条键位，但多光标这件事本身是它的强项：选区是一串
 * range，加一个 range 就是多一个光标。
 */
function addCursorVertically(direction: -1 | 1) {
  return (view: EditorView): boolean => {
    const ranges = view.state.selection.ranges

    const next = ranges.map(range => {
      const line = view.state.doc.lineAt(range.head)

      const pos =
        direction === -1
          ? Math.max(0, line.from - 1)
          : Math.min(view.state.doc.length, line.to + 1)

      return EditorSelection.cursor(pos, range.assoc)
    })

    view.dispatch({ selection: EditorSelection.create(next, ranges.length - 1) })

    return true
  }
}

/**
 * 宿主元素 → 编辑器实例。
 *
 * CodeMirror 6 **不把 view 挂在 DOM 上**（`EditorView.findFromDOM` 没有这号
 * API），但「在光标处插一段东西」「测试里改一改正文」都要拿到它 ⇒ 自己记一份。
 */
const views = new WeakMap<HTMLElement, EditorView>()

/** 从宿主元素取编辑器实例；取不到就是 null（还没挂上或已经卸了）。 */
export function noteEditorView(host: Element | null | undefined): EditorView | null {
  if (!host) {return null}

  return views.get(host as HTMLElement) ?? null
}

export interface NoteEditorProps {
  className?: string
  /** 正文：**挂载时读一次**。换一篇笔记请换 `key` 重挂（别拿它当受控值推）。 */
  initialValue: string
  /** 无障碍标签（屏读器念的那一句）。 */
  label?: string
  /** 每次改动都报一次（上层据此判「未保存」）。 */
  onChange: (value: string) => void
  /** `Ctrl+S`。 */
  onSave?: () => void
}

interface EditorStats {
  chars: number
  columns: number
  lines: number
  words: number
}

export function NoteEditor({ className, initialValue, label, onChange, onSave }: NoteEditorProps) {
  const { resolvedMode } = useTheme()
  const hostRef = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef<EditorView | null>(null)
  // 主题换的时候只重配这一层：重建编辑器会把光标位置与撤销栈一起丢掉。
  const themeConf = useRef(new Compartment())
  const [stats, setStats] = useState<EditorStats>({ chars: 0, columns: 1, lines: 1, words: 0 })

  // 回调放进 ref：编辑器只在挂载时建一次，闭包里的旧回调不该被记住。
  const onChangeRef = useRef(onChange)
  const onSaveRef = useRef(onSave)

  onChangeRef.current = onChange
  onSaveRef.current = onSave

  const isDark = resolvedMode === 'dark'

  const extensions = useMemo<Extension[]>(() => {
    const base: Extension[] = [
      lineNumbers(),
      highlightActiveLine(),
      highlightActiveLineGutter(),
      history(),
      drawSelection(),
      bracketMatching(),
      closeBrackets(),
      // markdown 语言层：标题、列表、引用、代码围栏都上色。
      markdown(),
      // 查找替换（Obsidian 的 Ctrl+F）：面板**落在编辑器顶部**（`top: true`），
      // 与 Obsidian 同位置。`search()` 这一层自带面板的样式与命中高亮。
      search({ top: true }),
      EditorView.lineWrapping,
      EditorState.tabSize.of(4),
      EditorView.contentAttributes.of({ spellcheck: 'false', ...(label ? { 'aria-label': label } : {}) }),
      keymap.of([
        { key: 'Mod-s', run: () => (onSaveRef.current?.(), true) },
        { key: 'Mod-f', run: openSearchPanel },
        { key: 'Mod-Alt-ArrowUp', run: addCursorVertically(-1) },
        { key: 'Mod-Alt-ArrowDown', run: addCursorVertically(1) },
        ...closeBracketsKeymap,
        // markdown 键位要排在 defaultKeymap **前面**：回车要续列表缩进、
        // 退格要删掉标记，而不是通用编辑器那套。
        ...markdownKeymap,
        ...defaultKeymap,
        ...historyKeymap,
        ...searchKeymap,
        indentWithTab
      ]),
      EditorView.updateListener.of(update => {
        if (update.docChanged) {
          onChangeRef.current(update.state.doc.toString())
        }

        const head = update.state.selection.main.head
        const line = update.state.doc.lineAt(head)
        const text = update.state.doc.toString()

        setStats({
          chars: text.length,
          columns: head - line.from + 1,
          lines: update.state.doc.lines,
          words: countWords(text)
        })
      })
    ]

    return base
  }, [label])

  useEffect(() => {
    const host = hostRef.current

    if (!host) {return}

    const view = new EditorView({
      state: EditorState.create({
        doc: initialValue,
        extensions: [...extensions, themeConf.current.of(githubEditorTheme(isDark))]
      }),
      parent: host
    })

    viewRef.current = view
    views.set(host, view)

    return () => {
      views.delete(host)
      view.destroy()
      viewRef.current = null
    }
    // 只在挂载时建一次：`initialValue` 与 `isDark` 都在别处处理（见下面的主题重配）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extensions])

  // 主题切换：只换那一层，不重建编辑器（重建会把光标与撤销栈一起丢掉）。
  useEffect(() => {
    const view = viewRef.current

    if (!view) {return}

    view.dispatch({ effects: themeConf.current.reconfigure(githubEditorTheme(resolvedMode === 'dark')) })
  }, [resolvedMode])

  return (
    <div className={cn('flex min-h-0 flex-1 flex-col', className)}>
      <div className="min-h-0 flex-1 overflow-hidden" data-testid="note-editor" ref={hostRef} />
      <div className="flex shrink-0 items-center gap-3 border-t border-(--ui-stroke-tertiary) px-1 py-1 text-[0.66rem] text-(--ui-text-tertiary) tabular-nums">
        <span title="字数">{stats.words} 字</span>
        <span title="字符数">{stats.chars} 字符</span>
        <span title="行与列">
          行 {stats.lines} · 列 {stats.columns}
        </span>
      </div>
    </div>
  )
}
