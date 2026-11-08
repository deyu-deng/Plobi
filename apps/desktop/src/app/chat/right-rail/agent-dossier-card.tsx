import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { fetchVaultAgent } from '@/app/surface/api'
import { CompactMarkdown } from '@/components/chat/compact-markdown'
import { Badge } from '@/components/ui/badge'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { AgentNotesList } from './notes-list'

function stripFrontmatter(text: string): string {
  if (!text.startsWith('---')) {
    return text
  }

  const end = text.indexOf('\n---', 3)

  return end < 0 ? text : text.slice(end + 4).replace(/^\s*\n/, '')
}

const SECTION = 'rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary)/40'

const SECTION_HEAD =
  'flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[0.68rem] font-medium text-foreground hover:bg-(--ui-control-hover-background)/50 cursor-pointer select-none'

interface AgentDossierCardProps {
  agentId: string
}

export function AgentDossierCard({ agentId }: AgentDossierCardProps) {
  const [open, setOpen] = useState<string | null>(null)
  const [notesOpen, setNotesOpen] = useState(false)
  const { t } = useI18n()
  const v = t.surface

  // **钩子必须在任何提前 return 之前调用**——agentId 一变就换钩子数 ⇒ React 崩。
  // 空 id 时用 `enabled: false` 让它不发请求，而不是先 return。
  const page = useQuery({
    queryKey: ['surface-agent', agentId],
    queryFn: () => fetchVaultAgent(agentId),
    enabled: Boolean(agentId),
    staleTime: 5 * 60 * 1000 // 5 分钟缓存
  })

  // 没有选中的 Agent ⇒ 空态
  if (!agentId) {
    return (
      <div className="p-4">
        <EmptyState description={v.emptyAgent} title={v.noAgentSelected} />
      </div>
    )
  }

  if (page.isError) {
    return (
      <div className="p-4">
        <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3">
          <Codicon className="mt-0.5 shrink-0 text-red-400" name="warning" size="0.875rem" />
          <div className="min-w-0 flex-1">
            <p className="text-[0.7rem] font-medium text-red-400">{v.agentLoadFailed}</p>
            <p className="mt-0.5 text-[0.62rem] leading-relaxed text-red-400/70">
              {(page.error instanceof Error ? page.error.message : undefined) ?? String(page.error)}
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (!page.data) {
    return (
      <div className="flex h-32 items-center justify-center">
        <div className="grid place-items-center">
          <Codicon className="animate-spin text-(--ui-text-tertiary)" name="loading" size="1rem" />
        </div>
      </div>
    )
  }

  const agent = page.data
  const docs = agent.docs.filter(doc => doc.exists)
  const firstOpen = open ?? docs[0]?.key ?? null
  const milestones = agent.milestones
  const pct = milestones.total > 0 ? (milestones.done / milestones.total) * 100 : 0

  return (
    <div className="p-4">
      {/* Agent header */}
      <div className="mb-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <h3 className="text-[0.9rem] font-semibold leading-tight">{agent.displayName}</h3>
          {agent.status && (
            <Badge variant={agent.status === 'active' ? 'default' : 'warn'}>
              {v.statuses[agent.status] ?? agent.status}
            </Badge>
          )}
        </div>
        {agent.description && (
          <p className="mt-1 text-[0.68rem] leading-relaxed text-(--ui-text-tertiary)">
            {agent.description}
          </p>
        )}
        {milestones.total > 0 && (
          <div className="mt-2 space-y-1">
            <div className="flex items-center justify-between text-[0.62rem] text-(--ui-text-tertiary)/70">
              <span>{v.milestones(milestones.done, milestones.total)}</span>
              <span className="tabular-nums">{Math.round(pct)}%</span>
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-(--ui-bg-quinary)">
              <div
                className="h-full rounded-full bg-emerald-500/70"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}
        <div className="mt-2 flex items-center gap-x-2 text-[0.6rem] text-(--ui-text-tertiary)/60">
          <span>
            {v.updated} {agent.updated || '—'}
          </span>
          {agent.workPath && (
            <span className="inline-flex min-w-0 items-center gap-1 truncate">
              <Codicon name="folder" size="0.68rem" />
              <span className="max-w-44 truncate font-mono">{agent.workPath}</span>
            </span>
          )}
        </div>
      </div>

      {/* Docs fold sections */}
      {docs.length === 0 ? (
        <div className="mt-3">
          <EmptyState description={v.notBuiltDesc(agent.docs[0]?.filename ?? 'plan.md')} title={v.notBuilt} />
        </div>
      ) : (
        <div className="mt-3 space-y-1.5">
          {docs.map(doc => {
            const expanded = firstOpen === doc.key

            return (
              <section className={SECTION} key={doc.key}>
                <button
                  aria-expanded={expanded}
                  className={SECTION_HEAD}
                  onClick={() => setOpen(expanded ? '' : doc.key)}
                  type="button"
                >
                  <Codicon
                    className="text-(--ui-text-tertiary)/60"
                    name={expanded ? 'chevron-down' : 'chevron-right'}
                    size="0.7rem"
                  />
                  <span>{v.docs[doc.key] ?? doc.label}</span>
                  <span className="ml-auto shrink-0 text-[0.58rem] font-normal tabular-nums text-(--ui-text-tertiary)/50">
                    {doc.updated}
                  </span>
                </button>
                {expanded && (
                  <div className={cn('border-t border-(--ui-stroke-tertiary) px-2.5 py-2')}>
                    <CompactMarkdown
                      className="text-[0.72rem] leading-relaxed text-(--ui-text-secondary)"
                      text={stripFrontmatter(doc.text)}
                    />
                  </div>
                )}
              </section>
            )
          })}
        </div>
      )}

      {/* 笔记：属于这个 Agent 的那一批（用户 11-07 拍板：笔记进档案卡片） */}
      <section className={cn(SECTION, 'mt-1.5')}>
        <button
          aria-expanded={notesOpen}
          className={SECTION_HEAD}
          onClick={() => setNotesOpen(!notesOpen)}
          type="button"
        >
          <Codicon
            className="text-(--ui-text-tertiary)/60"
            name={notesOpen ? 'chevron-down' : 'chevron-right'}
            size="0.7rem"
          />
          <span>{v.notesSection}</span>
        </button>
        {notesOpen && (
          <div className="border-t border-(--ui-stroke-tertiary) px-2 py-1.5">
            <AgentNotesList agentId={agentId} />
          </div>
        )}
      </section>
    </div>
  )
}

// Re-export for the main ChatView to use
function EmptyState({ description, title }: { description?: string; title: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <Codicon className="text-(--ui-text-tertiary)" name="notebook" size="1.5rem" />
      <p className="text-[0.7rem] font-medium text-foreground">{title}</p>
      {description && <p className="text-[0.62rem] text-(--ui-text-tertiary)">{description}</p>}
    </div>
  )
}
