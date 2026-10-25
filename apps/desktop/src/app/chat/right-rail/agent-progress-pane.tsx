import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { fetchVaultAgent } from '@/app/vault/api'
import { CompactMarkdown } from '@/components/chat/compact-markdown'
import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Codicon } from '@/components/ui/codicon'
import { CopyButton } from '@/components/ui/copy-button'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

function stripFrontmatter(text: string): string {
  if (!text.startsWith('---')) return text
  const end = text.indexOf('\n---', 3)
  return end < 0 ? text : text.slice(end + 4).replace(/^\s*\n/, '')
}

const SECTION = 'rounded-lg border border-(--ui-stroke-tertiary)'
const SECTION_HEAD =
  'flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[0.68rem] font-medium text-foreground hover:bg-(--ui-control-hover-background)/50'

export function AgentProgressPane({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const v = t.vault
  const [open, setOpen] = useState<string | null>(null)

  const page = useQuery({
    queryKey: ['vault-agent', agentId],
    queryFn: () => fetchVaultAgent(agentId),
    staleTime: 0
  })

  if (page.isError) {
    return (
      <EmptyState
        description={page.error instanceof Error ? page.error.message : undefined}
        title={v.loadFailed}
      />
    )
  }
  if (!page.data) {
    return <PageLoader label={v.loading} />
  }

  const agent = page.data
  const docs = agent.docs.filter(doc => doc.exists)
  const firstOpen = open ?? docs[0]?.key ?? null
  const pct = agent.milestones.total > 0 ? (agent.milestones.done / agent.milestones.total) * 100 : 0

  return (
    <div className="h-full overflow-y-auto px-2.5 py-2">
      <header className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <h2 className="text-[0.9rem] font-semibold leading-tight">{agent.displayName}</h2>
          {agent.status && <Badge variant={agent.status === 'active' ? 'default' : 'warn'}>{v.statuses[agent.status] ?? agent.status}</Badge>}
        </div>
        {agent.description && (
          <p className="text-[0.68rem] leading-relaxed text-muted-foreground">{agent.description}</p>
        )}
        {agent.milestones.total > 0 && (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-[0.62rem] text-muted-foreground/70">
              <span>{v.milestones(agent.milestones.done, agent.milestones.total)}</span>
              <span className="tabular-nums">{Math.round(pct)}%</span>
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-(--ui-bg-quinary)">
              <div className="h-full rounded-full bg-emerald-500/70" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.6rem] text-muted-foreground/60">
          <span>
            {v.updated} {agent.updated || '—'}
          </span>
          {agent.workPath && (
            <span className="inline-flex min-w-0 items-center gap-1">
              <Codicon name="folder" size="0.68rem" />
              <span className="max-w-44 truncate font-mono">{agent.workPath}</span>
              <CopyButton appearance="icon" text={agent.workPath} title={v.copyPath} />
            </span>
          )}
        </div>
      </header>

      {!agent.hasHome ? (
        <div className="mt-3">
          <EmptyState description={v.noHomeDesc} title={v.noHome} />
        </div>
      ) : (
        <div className="mt-3 space-y-1.5">
          {docs.length === 0 && (
            <EmptyState description={v.notBuiltDesc(agent.docs[0]?.filename ?? 'plan.md')} title={v.notBuilt} />
          )}
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
                    className="text-muted-foreground/60"
                    name={expanded ? 'chevron-down' : 'chevron-right'}
                    size="0.7rem"
                  />
                  <span>{v.docs[doc.key] ?? doc.label}</span>
                  <span className="ml-auto shrink-0 text-[0.58rem] font-normal tabular-nums text-muted-foreground/50">
                    {doc.updated}
                  </span>
                </button>
                {expanded && (
                  <div className={cn('border-t border-(--ui-stroke-tertiary) px-2.5 py-2')}>
                    <CompactMarkdown
                      className="text-[0.72rem] leading-relaxed text-foreground/90"
                      text={stripFrontmatter(doc.text)}
                    />
                  </div>
                )}
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
