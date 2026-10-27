import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { fetchDiaryEntry, fetchDiaryIndex, todayISO } from './api'
import { DiaryEditor } from './editor'

function dayLabel(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short'
  })
}

export function DiaryView() {
  const { t } = useI18n()
  const d = t.diary
  const queryClient = useQueryClient()
  const [date, setDate] = useState(todayISO)

  const index = useQuery({ queryKey: ['vault-diary'], queryFn: fetchDiaryIndex, staleTime: 30_000 })
  const entry = useQuery({
    queryKey: ['vault-diary', date],
    queryFn: () => fetchDiaryEntry(date),
    staleTime: Infinity
  })

  // 今天过零点要跟上：这台可能开着好几天不关。
  useEffect(() => {
    if (!index.data) return
    setDate(current => (current === todayISO() ? current : current))
  }, [index.data])

  const entries = index.data?.entries ?? []
  const days = useMemo(() => {
    const seen = new Set(entries.map(row => row.date))
    if (!seen.has(todayISO())) seen.add(todayISO())
    return [...seen].sort().reverse()
  }, [entries])

  const metaByDate = useMemo(() => new Map(entries.map(row => [row.date, row])), [entries])

  return (
    <div className="flex h-full min-h-0 flex-col bg-(--ui-editor-surface-background)">
      <header className="flex shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-3 py-2 pt-[calc(var(--titlebar-height)+0.5rem)]">
        <h1 className="text-sm font-semibold">{d.title}</h1>
        <span className="text-[0.68rem] text-muted-foreground/70">{dayLabel(date)}</span>
        <span className="ml-auto flex items-center gap-1">
          <Button onClick={() => setDate(todayISO())} size="xs" variant={date === todayISO() ? 'secondary' : 'ghost'}>
            {d.today}
          </Button>
        </span>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="w-40 shrink-0 overflow-y-auto border-r border-(--ui-stroke-tertiary) py-1.5">
          {index.isLoading ? (
            <PageLoader label={d.entries} />
          ) : (
            days.map(day => {
              const meta = metaByDate.get(day)
              return (
                <button
                  className={cn(
                    'flex w-full flex-col gap-0.5 px-3 py-1.5 text-left transition-colors hover:bg-(--ui-control-hover-background)/60',
                    day === date && 'bg-(--ui-control-hover-background)'
                  )}
                  key={day}
                  onClick={() => setDate(day)}
                  type="button"
                >
                  <span className="text-[0.72rem] font-medium tabular-nums">{day.slice(5)}</span>
                  <span className="text-[0.6rem] text-muted-foreground/60">
                    {meta ? d.chars(meta.chars) : d.notWritten}
                  </span>
                </button>
              )
            })
          )}
        </aside>

        <section className="min-w-0 flex-1">
          {entry.isError ? (
            <EmptyState
              description={entry.error instanceof Error ? entry.error.message : undefined}
              title={d.loadFailed}
            />
          ) : !entry.data ? (
            <PageLoader label={d.title} />
          ) : (
            <DiaryEditor
              baseText={entry.data.text}
              date={date}
              exists={entry.data.exists}
              key={date}
              onSaved={() => {
                void queryClient.invalidateQueries({ queryKey: ['vault-diary'] })
                void queryClient.invalidateQueries({ exact: true, queryKey: ['vault-diary', date] })
              }}
            />
          )}
        </section>
      </div>
    </div>
  )
}
