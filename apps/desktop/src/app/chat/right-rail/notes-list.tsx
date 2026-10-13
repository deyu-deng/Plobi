import { useEffect, useState } from 'react'

import { getAgentNotes } from '@/app/console/api'
import type { AgentNote, AgentNotesView } from '@/app/console/types'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { cn } from '@/lib/utils'
import { RIGHT_RAIL_VIEW_PREVIEW, setRightRailView } from '@/store/layout'
import { setCurrentSessionPreviewTarget } from '@/store/preview'

type State =
  | { kind: 'error'; message: string }
  | { kind: 'loading' }
  | { kind: 'ready'; view: AgentNotesView }

const NOTE_ROW =
  'flex w-full min-w-0 items-baseline gap-1.5 rounded px-2 py-1 text-left text-[0.75rem] text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background) hover:text-foreground'

// The empty state is a stack of cards, not a line of grey text: today the real
// brain has 79 notes and none of them carry a `belongs:` tag, so "empty" is the
// first screen every user sees, and it has to say what to do about it.
// Exported because the rail's own "nothing open" state uses the same shape.
export const RAIL_CARD =
  'flex items-start gap-2 rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-control-hover-background)/40 px-2.5 py-2'

export const RAIL_CARD_TITLE = 'text-[0.75rem] font-medium text-foreground'

export const RAIL_CARD_BODY = 'pt-0.5 text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)'

export interface AgentNotesListProps {
  agentId: string
}

/**
 * Opening a note hands it to the rail's existing file preview instead of growing
 * a second markdown surface here: that preview already renders `.md` with
 * highlighted fences, opens as a closable tab, and its ⌘/Ctrl+L selection
 * shortcut is how a passage becomes context in the conversation — which is the
 * whole point of reading a note next to the agent you are asking about it.
 */
async function openNote(note: AgentNote): Promise<void> {
  const target = await normalizeOrLocalPreviewTarget(note.fsPath, null)

  if (!target) {
    return
  }

  setCurrentSessionPreviewTarget(target, 'file-browser', note.fsPath)
  setRightRailView(RIGHT_RAIL_VIEW_PREVIEW)
}

/**
 * WP-MIND-NOTES-VIEW: the brain's notes that declare they belong to one agent.
 *
 * A label projection, never a copy — the note stays in the folder the human chose.
 * The counts next to the list are load-bearing: "0 here, 79 scanned in the brain"
 * is a different statement than an empty box, and an unreachable brain is shown as
 * an error rather than as emptiness.
 */
export function AgentNotesList({ agentId }: AgentNotesListProps) {
  const { t } = useI18n()
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
    return (
      <p className={RAIL_CARD_BODY} data-testid="agent-notes-loading">
        {t.console.notes.loading}
      </p>
    )
  }

  if (state.kind === 'error') {
    return (
      <div className="flex flex-col gap-1.5 p-1" data-testid="agent-notes-error">
        <div className={RAIL_CARD}>
          <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name="warning" size="0.8125rem" />
          <div className="min-w-0">
            <p className={RAIL_CARD_TITLE}>{t.console.notes.brainUnreachable}</p>
            <p className={RAIL_CARD_BODY}>{state.message}</p>
          </div>
        </div>
      </div>
    )
  }

  const { view } = state

  if (view.notes.length === 0) {
    return (
      <div className="flex flex-col gap-1.5 p-1" data-testid="agent-notes-empty">
        <div className={RAIL_CARD}>
          <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name="tag" size="0.8125rem" />
          <div className="min-w-0">
            <p className={RAIL_CARD_TITLE}>{t.console.notes.emptyTitle}</p>
            <p className={RAIL_CARD_BODY}>{t.console.notes.emptyHowToBody(view.agent)}</p>
          </div>
        </div>
        <div className={RAIL_CARD}>
          <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name="note" size="0.8125rem" />
          <div className="min-w-0">
            <p className={RAIL_CARD_TITLE}>{t.console.notes.scanned(view.totals.scanned)}</p>
            <p className={RAIL_CARD_BODY}>
              {view.totals.with_frontmatter} · {view.note_roots.join(', ')}
            </p>
          </div>
        </div>
        {view.unknown_belongs.length > 0 && (
          <div className={RAIL_CARD} data-testid="agent-notes-unknown">
            <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name="warning" size="0.8125rem" />
            <div className="min-w-0">
              <p className={RAIL_CARD_BODY}>
                {t.console.notes.unknownBelongs(view.unknown_belongs.join('、'))}
              </p>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <ul className="flex flex-col gap-px" data-testid="agent-notes-list">
      {view.notes.map(note => (
        <li key={note.path}>
          <button
            className={NOTE_ROW}
            data-testid={`agent-note-${note.path}`}
            onClick={() => void openNote(note)}
            title={note.path}
            type="button"
          >
            <span className="min-w-0 flex-1 truncate">{note.title}</span>
            <span className="shrink-0 tabular-nums text-(--ui-text-tertiary)">{note.chars}</span>
          </button>
        </li>
      ))}
      {view.budget.truncated && (
        <li className={cn(RAIL_CARD_BODY, 'px-2 pt-1')} data-testid="agent-notes-truncated">
          {t.console.notes.truncated(view.notes.length)}
        </li>
      )}
      {view.unknown_belongs.length > 0 && (
        <li className={cn(RAIL_CARD_BODY, 'px-2 pt-1')} data-testid="agent-notes-unknown">
          {t.console.notes.unknownBelongs(view.unknown_belongs.join('、'))}
        </li>
      )}
    </ul>
  )
}
