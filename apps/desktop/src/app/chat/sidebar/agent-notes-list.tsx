import { useEffect, useState } from 'react'

import { getAgentNotes } from '@/app/console/api'
import type { AgentNotesView } from '@/app/console/types'
import { cn } from '@/lib/utils'

type State =
  | { kind: 'error'; message: string }
  | { kind: 'loading' }
  | { kind: 'ready'; view: AgentNotesView }

const NOTE_ROW =
  'flex min-w-0 items-baseline gap-1.5 rounded px-2 py-1 text-[0.75rem] text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background) hover:text-foreground'

const HINT = 'px-2 py-1 text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)'

export interface AgentNotesListProps {
  agentId: string
}

/**
 * WP-MIND-NOTES-VIEW: the brain's notes that declare they belong to this agent.
 *
 * The list is a label projection (`belongs:` in each note's own frontmatter), so
 * the counts next to it are load-bearing: "0 notes here, 79 scanned in the brain"
 * is a different statement than an empty box, and a brain we cannot reach is shown
 * as an error rather than as emptiness.
 */
export function AgentNotesList({ agentId }: AgentNotesListProps) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    let alive = true

    setState({ kind: 'loading' })
    getAgentNotes(agentId).then(
      view => {
        if (alive) {
          setState({ kind: 'ready', view })
        }
      },
      error => {
        if (alive) {
          setState({
            kind: 'error',
            message: String((error as Error)?.message ?? error)
          })
        }
      }
    )

    return () => {
      alive = false
    }
  }, [agentId])

  if (state.kind === 'loading') {
    return <p className={HINT} data-testid="agent-notes-loading">读脑中…</p>
  }

  if (state.kind === 'error') {
    return (
      <p className={cn(HINT, 'text-(--ui-text-secondary)')} data-testid="agent-notes-error">
        脑读不到：{state.message}
      </p>
    )
  }

  const { view } = state

  if (view.notes.length === 0) {
    return (
      <p className={HINT} data-testid="agent-notes-empty">
        这条分身还没有挂笔记（脑里已扫 {view.totals.scanned} 篇、{view.totals.with_frontmatter} 篇有标签位）。
        在笔记的 frontmatter 里写 <code>belongs: [{view.agent}]</code> 就会出现在这里。
      </p>
    )
  }

  return (
    <ul className="flex flex-col gap-px" data-testid="agent-notes-list">
      {view.notes.map(note => (
        <li className={NOTE_ROW} key={note.path} title={note.path}>
          <span className="min-w-0 flex-1 truncate">{note.title}</span>
          <span className="shrink-0 tabular-nums text-(--ui-text-tertiary)">{note.chars}</span>
        </li>
      ))}
      {view.budget.truncated && (
        <li className={HINT} data-testid="agent-notes-truncated">
          已到上限，只列前 {view.notes.length} 条
        </li>
      )}
      {view.unknown_belongs.length > 0 && (
        <li className={HINT} data-testid="agent-notes-unknown">
          有 {view.unknown_belongs.length} 个标签对不上已登记的名字：{view.unknown_belongs.join('、')}
        </li>
      )}
    </ul>
  )
}
