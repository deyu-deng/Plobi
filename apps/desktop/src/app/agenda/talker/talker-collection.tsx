/**
 * Talker collection UI (A7) — mounted inside a `Dialog` from the board.
 *
 * Four states (loading / error / data) plus two data sub-views:
 * - pre-review (`reviewComplete === false`): a "未开始采集" banner + the
 *   backlog of recent talkers for one-click collect/exclude, closed by
 *   "完成审查并开始采集".
 * - post-review: the `pending` talkers (fail-closed new sessions) for
 *   one-click collect/exclude, or an empty state when none remain.
 *
 * Reuse: `PageLoader` / `PanelEmpty` / `PanelAction` / `PanelPill` /
 * `PanelSectionLabel` from `@/app/overlays/panel`, `Button` + `Codicon`. No
 * new visual language — same row chrome as the board/agent rails.
 */

import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { PanelAction, PanelEmpty, PanelPill, type PanelPillTone, PanelSectionLabel } from '@/app/overlays/panel'
import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'

import type { TalkerState, TalkerStatus } from './api'
import { SourceLiveness } from './source-liveness'
import {
  $talkerError,
  $talkerLoading,
  $talkerState,
  collectTalker,
  excludeTalker,
  excludeTalkers,
  finishReview,
  refreshTalkerCollection
} from './store'
import { KIND_ORDER, type TalkerKind, talkerKind } from './talker-kind'

const MODE_TONE: Record<TalkerStatus, PanelPillTone> = {
  excluded: 'bad',
  known: 'good',
  pending: 'muted'
}

function TalkerRow({
  busy,
  name,
  onCollect,
  onExclude,
  status
}: {
  busy: boolean
  name: string
  onCollect: () => void
  onExclude: () => void
  status: TalkerStatus
}) {
  const { t } = useI18n()
  const c = t.agenda.collect

  return (
    <div className="group/row row-hover relative flex h-7 w-full items-center gap-2 rounded-md pl-2 pr-1 text-[0.78rem] hover:text-foreground">
      <span className="min-w-0 flex-1 truncate font-medium text-foreground/85">{name}</span>
      {status !== 'pending' ? (
        <PanelPill tone={MODE_TONE[status]}>{status === 'known' ? c.modeKnown : c.modeExcluded}</PanelPill>
      ) : null}
      <PanelAction disabled={busy} icon="check" onClick={onCollect}>
        {c.collect}
      </PanelAction>
      <PanelAction disabled={busy} icon="discard" onClick={onExclude}>
        {c.exclude}
      </PanelAction>
    </div>
  )
}

function talkersByKind(talkers: TalkerState[]): Record<TalkerKind, TalkerState[]> {
  const buckets: Record<TalkerKind, TalkerState[]> = { direct: [], group: [], official: [] }

  for (const talker of talkers) {
    buckets[talkerKind(talker.id)].push(talker)
  }

  return buckets
}

/**
 * R-021: the review list, split into 私聊 / 群聊 / 公众号. Empty sections are
 * omitted so a fresh install (no groups yet) stays quiet. The official section
 * gets a "排除全部待定公众号" shortcut — it only ever targets `pending` ids, so
 * what the user already collected/excluded is never rewritten.
 */
function TalkerSections({
  busy,
  busyId,
  labels,
  onAct,
  onExcludeAllOfficial,
  talkers
}: {
  busy: boolean
  busyId: null | string
  labels: {
    excludeAllPendingOfficial: string
    sectionDirect: string
    sectionGroups: string
    sectionOfficial: string
  }
  onAct: (id: string, mode: 'collect' | 'exclude') => void
  onExcludeAllOfficial: () => void
  talkers: TalkerState[]
}) {
  const buckets = talkersByKind(talkers)
  // R-021 follow-up: every section folds away, and an open section is height-
  // capped with its own scroll — a 300-conversation backlog no longer turns the
  // dialog into one giant page scroll.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const KIND_LABEL: Record<TalkerKind, string> = {
    direct: labels.sectionDirect,
    group: labels.sectionGroups,
    official: labels.sectionOfficial
  }

  return (
    <div className="space-y-3">
      {KIND_ORDER.filter(kind => buckets[kind].length > 0).map(kind => {
        const pendingOfficial =
          kind === 'official' ? buckets.official.filter(talker => talker.status === 'pending') : []
        const open = !collapsed[kind]

        return (
          <div className="space-y-1" key={kind}>
            <div className="flex items-center gap-1">
              <button
                aria-expanded={open}
                className="flex min-h-6 min-w-0 flex-1 items-center gap-1.5 rounded-md px-1 py-0.5 text-left hover:bg-accent"
                onClick={() => setCollapsed(prev => ({ ...prev, [kind]: !prev[kind] }))}
                type="button"
              >
                <Codicon
                  className="shrink-0 text-muted-foreground"
                  name={open ? 'chevron-down' : 'chevron-right'}
                  size="0.75rem"
                />
                <span className="min-w-0 flex-1 truncate text-[0.6rem] font-medium uppercase tracking-wider text-muted-foreground/50">
                  {KIND_LABEL[kind]}
                </span>
                <span className="shrink-0 text-[0.65rem] tabular-nums text-muted-foreground/70">
                  {buckets[kind].length}
                </span>
              </button>
              {pendingOfficial.length > 0 ? (
                <Button disabled={busy} onClick={onExcludeAllOfficial} size="xs" variant="ghost">
                  {labels.excludeAllPendingOfficial}
                </Button>
              ) : null}
            </div>
            {open ? (
              <div className="max-h-64 space-y-0.5 overflow-y-auto pr-0.5">
                {buckets[kind].map(talker => (
                  <TalkerRow
                    busy={busyId === talker.id}
                    key={talker.id}
                    name={talker.name}
                    onCollect={() => onAct(talker.id, 'collect')}
                    onExclude={() => onAct(talker.id, 'exclude')}
                    status={talker.status}
                  />
                ))}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

export function TalkerCollection({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const c = t.agenda.collect
  const state = useStore($talkerState)
  const loading = useStore($talkerLoading)
  const error = useStore($talkerError)
  const [busyId, setBusyId] = useState<null | string>(null)

  useEffect(() => {
    void refreshTalkerCollection()
  }, [])

  if (loading && !state) {
    return <PageLoader label={c.title} />
  }

  if (error && !state) {
    return (
      <PanelEmpty
        action={
          <Button onClick={() => void refreshTalkerCollection()} size="xs" variant="ghost">
            {c.retry}
          </Button>
        }
        description={error}
        icon="warning"
        title={c.loadFailed}
      />
    )
  }

  if (!state) {
    return null
  }

  const pending = state.talkers.filter(talker => talker.status === 'pending')

  if (!state.reviewComplete) {
    return (
      <div className="space-y-3">
        <SourceLiveness labels={c.liveness} names={t.agenda.sources} />
        <PanelEmpty
          description={c.notStartedDesc}
          icon="shield"
          title={c.notStartedTitle}
        />

        <PanelSectionLabel className="px-1">{c.backlogTitle}</PanelSectionLabel>
        <TalkerSections
          busy={busyId !== null}
          busyId={busyId}
          labels={c}
          onAct={(id, mode) => void act(id, mode)}
          onExcludeAllOfficial={() => void actExcludeAllOfficial()}
          talkers={state.talkers}
        />

        <div className="flex justify-end pt-1">
          <Button disabled={busyId !== null} onClick={() => void actFinish()} size="sm">
            {c.finishReview}
          </Button>
        </div>
      </div>
    )
  }

  if (pending.length === 0) {
    return (
      <div className="space-y-3">
        <SourceLiveness labels={c.liveness} names={t.agenda.sources} />
        <PanelEmpty
          action={
            <Button onClick={onClose} size="xs" variant="ghost">
              {t.common.close}
            </Button>
          }
          description={c.pendingEmptyDesc}
          icon="checklist"
          title={c.pendingEmptyTitle}
        />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <SourceLiveness labels={c.liveness} names={t.agenda.sources} />
      <PanelSectionLabel className="px-1">{c.pendingTitle}</PanelSectionLabel>
      <TalkerSections
        busy={busyId !== null}
        busyId={busyId}
        labels={c}
        onAct={(id, mode) => void act(id, mode)}
        onExcludeAllOfficial={() => void actExcludeAllOfficial()}
        talkers={pending}
      />
    </div>
  )

  async function actExcludeAllOfficial() {
    // Only undecided official accounts — anything already known/excluded stays.
    const ids = (state?.talkers ?? [])
      .filter(talker => talker.status === 'pending' && talkerKind(talker.id) === 'official')
      .map(talker => talker.id)

    if (ids.length === 0) {
      return
    }

    setBusyId('__official__')

    try {
      await excludeTalkers(ids)
    } finally {
      setBusyId(null)
    }
  }

  async function act(id: string, mode: 'collect' | 'exclude') {
    setBusyId(id)

    try {
      if (mode === 'collect') {
        await collectTalker(id)
      } else {
        await excludeTalker(id)
      }
    } finally {
      setBusyId(null)
    }
  }

  async function actFinish() {
    setBusyId('__review__')

    try {
      await finishReview()
    } finally {
      setBusyId(null)
    }
  }
}
