/**
 * 设置 → 信源：一条信源一行，**行上只有一颗开关**，其余信息在点开的抽屉里。
 *
 * 10-22 用户对着这一屏说的原话：「**一个信源就一个开关按钮，就三种形态：未连接和连接，
 * 然后出错了会有一个报错。不要有这么多按钮**」＋「**第一次点击就引导配置跳转到相应的界面。
 * 然后点击一个信源抽屉就会展开，就会显示相关信息**」。这一版就是那两句：
 *
 * 1. **状态是后端算的三态**（`sources_api.status_of`），这里只画一个点与一个词；
 *    出错时多一行**一句人话原因**（后端给原因码，四语在 `settings.sources.reasons` 里查，
 *    用户 10-22：「人话一句，但是语言产品化一点」）。执行方逐字那句原话**进抽屉**，
 *    不上行（P6 那条"不转述"仍然成立：原话一个字没改，只是挪了位置）。正常态那一行什么都不多写。
 * 2. **行上只剩一颗开关**。旧版那三颗文字按钮（「首次配置」「试一次」「停掉」）里，
 *    「停掉」就是开关的 off，「试一次」搬进抽屉改名「立即同步」，「首次配置」变成
 *    **点这一行**展开的那片抽屉。
 * 3. **必填没填时，点开关不动开关，直接展开抽屉并聚焦第一格** —— 一颗"开了但什么都没发生"
 *    的开关是这一屏最容易骗人的一种形状。
 * 4. **主题随应用走**：全程用仓里那套件（`ListRow` / `Switch` / `Pill` / `SectionHeading`），
 *    零字面色值。
 */

import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { Cpu, FolderOpen, Globe, Layers3, MessageCircle, Package, Users } from '@/lib/icons'
import { notify, notifyError } from '@/store/notifications'

import { ListRow, SectionHeading, SettingsContent } from './primitives'
import { getSources, setSourceEnabled, type SourceRow, type SourcesPayload, trySource } from './sources/api'
import { ExternalSources } from './sources/external-sources'
import { SetupFields } from './sources/setup-fields'
import { SourceAvatar } from './sources/source-icon'
import { groupByCategory, needsSetup, statusOf, statusReason, visibleGroups } from './sources/verbs'

type Labels = ReturnType<typeof useI18n>['t']['settings']['sources']

/** 分类 → 图标。**不用字母或序号代替图标** —— 旁边就是设置里其他分类的写法。 */
const CATEGORY_ICON: Record<string, typeof Globe> = {
  custom: Package,
  local: FolderOpen,
  message: MessageCircle,
  social: Users,
  system: Cpu,
  website: Globe
}

export function SourcesSettings() {
  const labels = useI18n().t.settings.sources

  const [payload, setPayload] = useState<null | SourcesPayload>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState<null | string>(null)
  const [open, setOpen] = useState<null | string>(null)
  // 只有"因为缺配置而被引导进抽屉"那一次要自动聚焦；单纯点开抽屉不该抢光标。
  const [guide, setGuide] = useState<null | string>(null)

  const reload = useCallback(async () => {
    try {
      setPayload(await getSources())
      setLoadFailed(false)
    } catch (err) {
      // 读不到就老实说读不到。画一个空面板等于说"一条信源都没有"——那是两句不同的话。
      // 原因进控制台与通知，不上屏（同旧版那条规矩）。
      console.error('[settings/sources] getSources failed', err)
      setLoadFailed(true)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const onToggle = async (row: SourceRow, enabled: boolean) => {
    // 第一次点开关而它必填没填 ⇒ **引导去配置**，不是把开关拨上去再报"没反应"。
    if (enabled && needsSetup(row)) {
      setOpen(row.source)
      setGuide(row.source)

      return
    }

    setBusy(row.source)

    try {
      await setSourceEnabled(row.source, enabled)
      await reload()
    } catch (err) {
      notifyError(err, labels.switchFailed)
    } finally {
      setBusy(null)
    }
  }

  const onTry = async (row: SourceRow) => {
    setBusy(row.source)

    try {
      const result = await trySource(row.source)
      await reload()
      // 逐字显示执行方自己那句话。这一层不转述、不总结（P6）。
      notify({ kind: result.ok ? 'success' : 'error', message: result.detail })
    } catch (err) {
      notifyError(err, labels.tryFailed)
    } finally {
      setBusy(null)
    }
  }

  if (loadFailed || !payload || !Array.isArray(payload.sources) || !Array.isArray(payload.categories)) {
    // 双保险：api 层已经校验过形状（见 ./sources/api.ts），这里再挡一道——
    // 一个字段缺失曾经把整个应用崩进根 error boundary。
    return (
      <SettingsContent>
        <SectionHeading icon={Layers3} title={labels.title} />
        <p className="text-sm text-muted-foreground" data-testid="sources-failed">{labels.loadFailed}</p>
      </SettingsContent>
    )
  }

  const groups = visibleGroups(groupByCategory(payload.sources, payload.categories))

  return (
    <SettingsContent>
      <SectionHeading icon={Layers3} meta={String(payload.sources.length)} title={labels.title} />

      {groups.map(group => (
        <section className="mt-4" data-testid={`source-group-${group.category}`} key={group.category}>
          <SectionHeading
            icon={CATEGORY_ICON[group.category] ?? Layers3}
            title={labels.categories[group.category as keyof Labels['categories']] ?? group.category}
          />

          {group.category === 'custom' ? <ExternalSources labels={labels} onChanged={reload} /> : null}

          {group.rows.map(row => (
            <SourceRowView
              busy={busy === row.source}
              guiding={guide === row.source}
              key={row.source}
              labels={labels}
              onActivate={() => {
                setOpen(open === row.source ? null : row.source)
                setGuide(null)
              }}
              onReload={reload}
              onToggle={onToggle}
              onTry={onTry}
              open={open === row.source}
              row={row}
            />
          ))}
        </section>
      ))}
    </SettingsContent>
  )
}

function SourceRowView({
  busy,
  guiding,
  labels,
  onActivate,
  onReload,
  onTry,
  onToggle,
  open,
  row
}: {
  busy: boolean
  guiding: boolean
  labels: Labels
  onActivate: () => void
  onReload: () => Promise<void>
  onTry: (row: SourceRow) => Promise<void>
  onToggle: (row: SourceRow, enabled: boolean) => Promise<void>
  open: boolean
  row: SourceRow
}) {
  const { t } = useI18n()
  const status = statusOf(row)
  const displayName = t.agenda.sources[row.source as keyof typeof t.agenda.sources] ?? labels.unknownSource
  const checked = row.enabled ?? false

  return (
    <ListRow
      action={
        // 行上**只有这一颗**。旧版这里最多三颗文字按钮，用户的话是「不要有这么多按钮」。
        <Switch
          aria-label={`${displayName} — ${labels[STATUS_KEY[status]]}`}
          checked={checked}
          disabled={busy || row.enabled === null}
          onCheckedChange={next => { void onToggle(row, next) }}
          size="xs"
        />
      }
      below={open
        ? <SourceDrawer busy={busy} guiding={guiding} labels={labels} onReload={onReload} onTry={onTry} row={row} />
        : undefined}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span aria-hidden="true" className={STATUS_DOT[status]} />
          <span>{labels[STATUS_KEY[status]]}</span>
          {/* 只有出错态多这一行，且是**目录里那句人话**（码不上屏，裁定 45.4）。
              执行方逐字原话在抽屉里。正常态什么都不多写——用户 10-22：「不要有这么多东西」。 */
            status === 'error' ? <span className="text-destructive">{statusReason(row, labels)}</span> : null}
        </span>
      }
      expanded={open}
      onActivate={onActivate}
      title={
        // 图标 + 名字。图标优先用后端从这条**自己声明的网站**抓来的 favicon，
        // 没有就退本地品牌表，再退首字母（见 `sources/source-icon.tsx`）。
        <span className="flex min-w-0 items-center gap-2">
          <SourceAvatar
            category={row.category ?? undefined}
            displayName={displayName}
            iconUrl={row.icon || undefined}
            sourceId={row.source}
          />
          <span className="truncate">{displayName}</span>
        </span>
      }
    />
  )
}

const STATUS_KEY = {
  connected: 'statusConnected',
  disconnected: 'statusDisconnected',
  error: 'statusError'
} as const

const STATUS_DOT = {
  connected: 'size-1.5 rounded-full bg-primary',
  disconnected: 'size-1.5 rounded-full bg-(--ui-text-tertiary)',
  error: 'size-1.5 rounded-full bg-destructive'
} as const

/** 点开的那片抽屉：原因／上一次／下一次／池里几条／日程里几条／设置／立即同步。 */
function SourceDrawer({ busy, guiding, labels, onReload, onTry, row }: {
  busy: boolean
  guiding: boolean
  labels: Labels
  onReload: () => Promise<void>
  onTry: (row: SourceRow) => Promise<void>
  row: SourceRow
}) {
  const kinds = Object.entries(row.pool_kinds ?? {}).sort((a, b) => b[1] - a[1])

  return (
    <div className="mt-2 space-y-3 rounded-lg bg-(--ui-bg-secondary) p-3" data-testid={`source-drawer-${row.source}`}>
      <dl className="grid gap-x-4 gap-y-1 text-[length:var(--conversation-caption-font-size)] sm:grid-cols-2">
        <Detail label={labels.drawerReason} value={statusReason(row, labels)} />
        <Detail label={labels.drawerLastRun} value={row.last_run_at || labels.drawerNeverRun} />
        <Detail
          label={labels.drawerNextRun}
          value={row.enabled === false
            ? '—'
            : row.next_run_at || labels.drawerNextEveryRound}
        />
        <Detail
          label={labels.drawerPoolCount}
          value={row.pool_count === undefined || row.pool_count < 0
            ? labels.drawerPoolUnreadable
            : `${row.pool_count}${kinds.length > 0 ? `（${kinds.slice(0, 4).map(([kind, count]) => `${kind} ${count}`).join(' · ')}）` : ''}`}
        />
        <Detail label={labels.drawerAgendaCount} value={String(row.events)} />
      </dl>

      {/* 执行方逐字那一句留在这里，不进那一行：它是我们读给排障的人听的，不是给"这条源怎么了"
          那一格用的（用户 10-22：行上要人话、要产品化）。 */}
      {row.status_detail
        ? (
            <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`source-run-note-${row.source}`}>
              {labels.drawerRunNote}
              {' — '}
              {row.status_detail}
            </p>
          )
        : null}

      {row.setup_fields.length > 0
        ? <SetupFields busy={busy} focusFirst={guiding} labels={labels} onSaved={onReload} row={row} />
        : null}

      <Button
        disabled={busy}
        onClick={() => { void onTry(row) }}
        size="sm"
        title={labels.tryRunsForReal}
        variant="secondary"
      >
        {busy ? labels.tryRunning : labels.syncNow}
      </Button>
    </div>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-baseline gap-2">
      <dt className="shrink-0 text-(--ui-text-tertiary)">{label}</dt>
      <dd className="min-w-0 truncate">{value}</dd>
    </div>
  )
}
