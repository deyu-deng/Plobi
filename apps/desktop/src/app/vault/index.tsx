import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { DetailColumn, ListColumn, MasterDetail } from '@/app/master-detail'
import { PageSearchShell, type PageShellTab } from '@/app/page-search-shell'
import { CompactMarkdown } from '@/components/chat/compact-markdown'
import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { CopyButton } from '@/components/ui/copy-button'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { fetchVaultAgent, fetchVaultIndex } from './api'
import type { VaultAgent } from './types'

const CATEGORY_ORDER = ['projects', 'research', 'courses', 'butler', 'events'] as const

function statusDot(status: string): string {
  if (status === 'active') return 'bg-emerald-500'
  if (status === 'paused') return 'bg-amber-500'
  return 'bg-muted-foreground/40'
}

function statusVariant(status: string): 'default' | 'warn' | 'muted' {
  if (status === 'active') return 'default'
  if (status === 'paused') return 'warn'
  return 'muted'
}

function matches(agent: VaultAgent, needle: string): boolean {
  if (!needle) return true
  const hay = [agent.displayName, agent.name, agent.description, agent.summary, agent.categoryLabel, agent.workPath]
    .join('\n')
    .toLowerCase()
  return hay.includes(needle.toLowerCase())
}

function AgentRow({
  agent,
  active,
  categoryLabel,
  milestonesLabel,
  onSelect
}: {
  agent: VaultAgent
  active: boolean
  categoryLabel: string
  milestonesLabel: string | null
  onSelect: () => void
}) {
  const pct = agent.milestones.total > 0 ? (agent.milestones.done / agent.milestones.total) * 100 : 0

  return (
    <button
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-md px-2.5 py-2 text-left transition-colors',
        'hover:bg-(--ui-control-hover-background)/60',
        active && 'bg-(--ui-control-hover-background)'
      )}
      onClick={onSelect}
      type="button"
    >
      <div className="flex items-center gap-2">
        <span className={cn('size-1.5 shrink-0 rounded-full', statusDot(agent.status))} />
        <span className="min-w-0 truncate text-[0.8rem] font-medium">{agent.displayName}</span>
        <span className="ml-auto shrink-0 text-[0.62rem] tabular-nums text-muted-foreground/60">
          {agent.updated || '—'}
        </span>
      </div>
      <div className="line-clamp-2 text-[0.68rem] leading-snug text-muted-foreground/80">
        {agent.summary || agent.description || '—'}
      </div>
      <div className="flex items-center gap-2">
        <Badge variant="outline">{categoryLabel}</Badge>
        {milestonesLabel && (
          <span className="text-[0.62rem] tabular-nums text-muted-foreground/60">{milestonesLabel}</span>
        )}
        {pct > 0 && (
          <span className="h-1 flex-1 overflow-hidden rounded-full bg-(--ui-bg-quinary)">
            <span className="block h-full rounded-full bg-emerald-500/70" style={{ width: `${pct}%` }} />
          </span>
        )}
      </div>
    </button>
  )
}

export function VaultView() {
  const { t } = useI18n()
  const v = t.vault
  const [category, setCategory] = useState('all')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [docKey, setDocKey] = useState('')

  const index = useQuery({ queryKey: ['vault-index'], queryFn: fetchVaultIndex, staleTime: 0 })
  const agents = index.data?.agents ?? []

  const counts = useMemo(() => {
    const out: Record<string, number> = {}
    for (const agent of agents) out[agent.category] = (out[agent.category] ?? 0) + 1
    return out
  }, [agents])

  const tabs = useMemo<PageShellTab[]>(() => {
    const order = CATEGORY_ORDER.filter(id => (counts[id] ?? 0) > 0)
    return [
      { id: 'all', label: v.all, meta: agents.length },
      ...order.map(id => ({ id, label: v.categories[id] ?? id, meta: counts[id] ?? 0 }))
    ]
  }, [agents.length, counts, v.all, v.categories])

  const visible = useMemo(
    () =>
      agents.filter(
        agent => (category === 'all' || agent.category === category) && matches(agent, query.trim())
      ),
    [agents, category, query]
  )

  useEffect(() => {
    if (selected && visible.some(agent => agent.id === selected)) return
    setSelected(visible[0]?.id ?? null)
    setDocKey('')
  }, [selected, visible])

  const page = useQuery({
    queryKey: ['vault-agent', selected],
    queryFn: () => fetchVaultAgent(selected as string),
    enabled: Boolean(selected),
    staleTime: 0
  })

  useEffect(() => {
    setDocKey('')
  }, [selected])

  const detail = page.data
  const docs = detail?.docs ?? []
  const activeDoc = docs.find(doc => doc.key === docKey) ?? docs.find(doc => doc.exists) ?? docs[0]
  const milestonesLabel = detail?.milestones.total
    ? v.milestones(detail.milestones.done, detail.milestones.total)
    : null

  const move = (delta: number) => {
    if (!visible.length) return
    const at = visible.findIndex(agent => agent.id === selected)
    const next = visible[Math.min(visible.length - 1, Math.max(0, (at < 0 ? 0 : at) + delta))]
    if (next) {
      setSelected(next.id)
      setDocKey('')
    }
  }

  const body = index.isError ? (
    <EmptyState
      description={index.error instanceof Error ? index.error.message : undefined}
      title={v.loadFailed}
    />
  ) : index.isLoading ? (
    <PageLoader label={v.loading} />
  ) : visible.length === 0 ? (
    <EmptyState description={agents.length ? v.noMatchDesc : v.emptyDesc} title={agents.length ? v.noMatchTitle : v.emptyTitle} />
  ) : (
    <MasterDetail split="wide">
      <ListColumn>
        <div
          className="flex h-full flex-col gap-0.5 outline-none"
          onKeyDown={event => {
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              move(1)
            } else if (event.key === 'ArrowUp') {
              event.preventDefault()
              move(-1)
            }
          }}
          tabIndex={0}
        >
          {visible.map(agent => (
            <AgentRow
              active={agent.id === selected}
              agent={agent}
              categoryLabel={v.categories[agent.category] ?? agent.categoryLabel}
              key={agent.id}
              milestonesLabel={
                agent.milestones.total ? v.milestones(agent.milestones.done, agent.milestones.total) : null
              }
              onSelect={() => {
                setSelected(agent.id)
                setDocKey('')
              }}
            />
          ))}
        </div>
      </ListColumn>

      <DetailColumn
        footer={
          detail ? (
            <span>
              {v.readOnly} · {detail.homeLabel || '—'}
            </span>
          ) : null
        }
      >
        {!detail ? (
          page.isError ? (
            <EmptyState
              description={page.error instanceof Error ? page.error.message : undefined}
              title={v.loadFailed}
            />
          ) : (
            <PageLoader label={v.loading} />
          )
        ) : (
          <div className="space-y-4">
            <header className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold leading-tight">{detail.displayName}</h1>
                <Badge variant="outline">{v.categories[detail.category] ?? detail.categoryLabel}</Badge>
                {detail.status && (
                  <Badge variant={statusVariant(detail.status)}>{v.statuses[detail.status] ?? detail.status}</Badge>
                )}
              </div>
              {detail.description && (
                <p className="text-xs leading-relaxed text-muted-foreground">{detail.description}</p>
              )}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.66rem] text-muted-foreground/70">
                <span>
                  {v.updated} {detail.updated || '—'}
                </span>
                {milestonesLabel && <span>{milestonesLabel}</span>}
                {detail.workPath && (
                  <span className="inline-flex items-center gap-1">
                    <Codicon name="folder" size="0.7rem" />
                    <span className="max-w-56 truncate font-mono">{detail.workPath}</span>
                    <CopyButton appearance="icon" text={detail.workPath} title={v.copyPath} />
                  </span>
                )}
              </div>
            </header>

            {!detail.hasHome ? (
              <EmptyState description={v.noHomeDesc} title={v.noHome} />
            ) : (
              <>
                <div className="flex flex-wrap gap-1.5">
                  {docs.map(doc => (
                    <button
                      className={cn(
                        'rounded-md px-2 py-1 text-[0.7rem] transition-colors',
                        doc.exists ? 'hover:bg-(--ui-control-hover-background)' : 'opacity-45',
                        activeDoc?.key === doc.key
                          ? 'bg-(--ui-control-hover-background) font-medium'
                          : 'text-muted-foreground'
                      )}
                      disabled={!doc.exists}
                      key={doc.key}
                      onClick={() => setDocKey(doc.key)}
                      type="button"
                    >
                      {v.docs[doc.key] ?? doc.label}
                    </button>
                  ))}
                </div>

                {activeDoc && activeDoc.exists ? (
                  <div className="rounded-lg border border-(--ui-stroke-tertiary)">
                    <div className="flex flex-wrap items-center gap-x-2 border-b border-(--ui-stroke-tertiary) px-3 py-1.5 text-[0.62rem] text-muted-foreground/60">
                      <span className="font-mono">{activeDoc.filename}</span>
                      <span>{activeDoc.chars} 字符</span>
                      {activeDoc.updated && <span>{v.updated} {activeDoc.updated}</span>}
                      {activeDoc.truncated && <Badge variant="warn">truncated</Badge>}
                    </div>
                    <CompactMarkdown
                      className="px-3 py-3 text-sm leading-relaxed text-foreground"
                      text={activeDoc.text}
                    />
                  </div>
                ) : (
                  <EmptyState
                    description={activeDoc ? v.notBuiltDesc(activeDoc.filename) : undefined}
                    title={v.notBuilt}
                  />
                )}
              </>
            )}
          </div>
        )}
      </DetailColumn>
    </MasterDetail>
  )

  return (
    <PageSearchShell
      activeTab={category}
      onSearchChange={setQuery}
      onTabChange={setCategory}
      searchPlaceholder={v.searchPlaceholder}
      searchTrailingAction={
        <Button
          aria-label={v.refresh}
          onClick={() => void index.refetch()}
          size="icon-titlebar"
          title={v.refresh}
          variant="ghost"
        >
          <Codicon name="refresh" size="0.85rem" spinning={index.isFetching} />
        </Button>
      }
      searchValue={query}
      tabs={tabs}
    >
      {body}
    </PageSearchShell>
  )
}
