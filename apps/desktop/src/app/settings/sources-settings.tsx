/**
 * 设置 → 信源：按分类分组的信源，每条一行（`ListRow`）+ 一个状态胶囊 + 一个动词。
 *
 * 用户 10-19 第二轮拍定：**界面上只留胶囊，其余全删。** 这一版的规矩：
 *
 * 1. **只说测到的最短那一句。** 状态是一个 `Pill`（「正在采集」/「停了」/「没开」…）。
 *    后端那句工程说明、什么时候进的账、「不猜」那段自白——**一律不上屏**：它们进日志
 *    与通知，不进界面。上一版把 `note` 整段渲染出来（连 `**加粗**` 的星号都在界面上），
 *    那是把内部文档贴到了用户脸上。
 * 2. **胶囊与按钮同源。** 两者都从 `sources/verbs.ts` 那一张表来（`pillFor` / `verbFor`），
 *    分开算就会出现「胶囊说开着、按钮说试一次」那种自相矛盾的一行。
 * 3. **空分类整块不画。** 见 `visibleGroups`。
 * 4. **主题随应用走。** 全程用仓里那套件（`ListRow` / `Pill` / `SectionHeading` /
 *    `EmptyState`），零字面色值。
 */

import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Cpu, FolderOpen, Globe, Layers3, MessageCircle, Package, Play, Users } from '@/lib/icons'
import { notify, notifyError } from '@/store/notifications'

import { ListRow, Pill, SectionHeading, SettingsContent } from './primitives'
import { getSources, setSourceEnabled, type SourceRow, type SourcesPayload, trySource } from './sources/api'
import { ExternalSources } from './sources/external-sources'
import { SetupFields } from './sources/setup-fields'
import { SourceAvatar } from './sources/source-icon'
import { groupByCategory, needsSetup, pillFor, pillTone, verbFor, visibleGroups } from './sources/verbs'

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

  const reload = useCallback(async () => {
    try {
      setPayload(await getSources())
      setLoadFailed(false)
    } catch (err) {
      // 读不到就老实说读不到。画一个空面板等于说"一条信源都没有"——那是两句不同的话。
      // 不把 err 写上面的规矩同本节注释：原因进控制台与通知，不上屏。
      console.error('[settings/sources] getSources failed', err)
      setLoadFailed(true)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const onToggle = async (row: SourceRow, enabled: boolean) => {
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
      // 逐字显示执行方自己那句话。这一层不转述、不总结（P6）——跑完的细节看这里，
      // 而设置页那一行只负责说"它现在什么状态"。
      notify({ kind: result.ok ? 'success' : 'error', message: result.detail })
    } catch (err) {
      notifyError(err, labels.tryFailed)
    } finally {
      setBusy(null)
    }
  }

  if (loadFailed) {
    return (
      <SettingsContent>
        <SectionHeading icon={Layers3} title={labels.title} />
        <p className="text-sm text-muted-foreground" data-testid="sources-failed">{labels.loadFailed}</p>
      </SettingsContent>
    )
  }

  // 双保险：api 层已经校验过形状（见 ./sources/api.ts 的 parseSourcesPayload），
  // 但这一层再加一道——一个字段缺失曾经把整个应用崩进根 error boundary，而那正是
  // 「绝不许因为一格数据把用户面前的东西全弄没」的形状。
  if (
    !payload ||
    !Array.isArray(payload.sources) ||
    !Array.isArray(payload.categories)
  ) {
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
              key={row.source}
              labels={labels}
              onReload={reload}
              onToggle={onToggle}
              onTry={onTry}
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
  labels,
  onReload,
  onTry,
  onToggle,
  row
}: {
  busy: boolean
  labels: Labels
  onReload: () => Promise<void>
  onTry: (row: SourceRow) => Promise<void>
  onToggle: (row: SourceRow, enabled: boolean) => Promise<void>
  row: SourceRow
}) {
  const { t } = useI18n()
  const [setupOpen, setSetupOpen] = useState(false)
  const verb = verbFor(row)
  const setupPending = needsSetup(row)
  const displayName = t.agenda.sources[row.source as keyof typeof t.agenda.sources] ?? labels.unknownSource

  return (
    <ListRow
      action={
        <div className="flex items-center justify-end gap-1.5">
          {row.setup_fields.length > 0 ? (
            <Button
              aria-expanded={setupOpen}
              onClick={() => { setSetupOpen(open => !open) }}
              size="sm"
              variant="textStrong"
            >
              {setupPending ? labels.setupNeeded : labels.setup}
            </Button>
          ) : null}

          <Pill tone={pillTone(row)}>{labels[pillFor(row)]}</Pill>

          {verb === 'try' ? (
            <Button
              disabled={busy}
              onClick={() => { void onTry(row) }}
              size="sm"
              title={labels.tryRunsForReal}
              variant="secondary"
            >
              <Play className="mr-1.5 size-3.5" />
              {busy ? labels.tryRunning : labels.tryOnce}
            </Button>
          ) : verb === 'enable' || verb === 'disable' ? (
            <Button
              disabled={busy || row.enabled === null}
              onClick={() => { void onToggle(row, verb === 'enable') }}
              size="sm"
              variant="secondary"
            >
              {verb === 'enable' ? labels.enable : labels.disable}
            </Button>
          ) : null}
        </div>
      }
      title={
        // 名字 + 图标一起（用户 10-19 第三轮）。图标在前、名字在后，与会话列表那一族
        // 同形；查不到图标的那一条退回首字母，见 `sources/source-icon.tsx`。
        <span className="flex min-w-0 items-center gap-2">
          <SourceAvatar displayName={displayName} sourceId={row.source} />
          <span className="truncate">{displayName}</span>
        </span>
      }
      {...(setupOpen && row.setup_fields.length > 0
        ? { below: <SetupFields busy={busy} labels={labels} onSaved={onReload} row={row} /> }
        : {})}
    />
  )
}