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
 * 5. **大类可以折叠**（用户 10-25：「不同大类的信源也可以折叠起来」）：每段的头按得动，
 *    收起时那一段的行**根本不画**（不是 `display:none`），头那格仍留「条数 · N 条报错」——
 *    折起来还能扫出哪一段有问题，这才是折叠的价值。三态与落点见 `./sources/collapse.ts`。
 */

import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { useI18n } from '@/i18n'
import { selectDesktopPaths } from '@/lib/desktop-fs'
import { Layers3 } from '@/lib/icons'
import { notify, notifyError } from '@/store/notifications'

import { ConnectionControl } from './connection-control'
import { ListRow, SectionHeading, SettingsContent } from './primitives'
import { getSources, importSourceFile, setSourceEnabled, type SourceRow, type SourcesPayload, trySource } from './sources/api'
import { $fold, decide, isOpen } from './sources/collapse'
import { ConversationList } from './sources/conversation-list'
import { ExternalSources } from './sources/external-sources'
import { firstRunLabels, type FirstRunLabels } from './sources/first-run-labels'
import { SetupFields } from './sources/setup-fields'
import { parseCadence } from './sources/setup-fields'
import { SourceAvatar } from './sources/source-icon'
import { $tags, keepsRow, tagOptions, toggleTag } from './sources/tags'
import { groupByCategory, needsSetup, relativeTime, statusOf, statusReason, visibleGroups } from './sources/verbs'

type Labels = ReturnType<typeof useI18n>['t']['settings']['sources']

export function SourcesSettings() {
  const i18n = useI18n()
  const labels = i18n.t.settings.sources
  const fr = firstRunLabels(i18n.locale)

  const [payload, setPayload] = useState<null | SourcesPayload>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState<null | string>(null)
  const [open, setOpen] = useState<null | string>(null)
  // 只有"因为缺配置而被引导进抽屉"那一次要自动聚焦；单纯点开抽屉不该抢光标。
  const [guide, setGuide] = useState<null | string>(null)
  // 折叠状态是一个订阅中的 store ⇒ 必须和其他 hook 一样在**任何提前 return 之前**取，
  // 不然"读数还没到"那一趟与到了那一趟的 hook 数量不一致，React 直接抛。
  const fold = useStore($fold)
  // 顶部那一排标签＝"只看这几种形状"。它是**浏览决定**，与折叠同一档持久化（本机记住、
  // 不进 `sources.yaml`）；形状的真源是池里那条 `kind`，这里不另建一处（10-27 那条口径）。
  const tags = useStore($tags)

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
      const sidecar = await setSourceEnabled(row.source, enabled)
      await reload()

      // 这一条有本机服务 ⇒ 开关说了"开/关"还不够，得说那个进程现在到底活没活。
      // 句子由界面拼（后端给的是读数 `listening`，裁定 45.4）；后端那句中文原文进控制台。
      if (sidecar) {
        console.info('[settings/sources] sidecar', row.source, sidecar.action, sidecar.detail)
        notify({
          kind: sidecar.listening === enabled ? 'success' : 'error',
          // 几种不同的病不许并成一句（用户 10-25 拍「只把话说清」）：光说"未能启动"，
          // 他分不清是没装构建工具、没产物、没密钥还是端口没起。准备那一步的话由后端给。
          message: sidecarOutcomeLabel(sidecar.listening, enabled, labels)
            + (sidecar.prepare ? `；${sidecar.prepare}` : '')
        })
      }
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

  const onImport = async () => {
    // 系统选文件框（用户自己点的那一下），扩展名闸门在框里就先挡一道；
    // 后端还会再拒一次并说清支持哪几种——两道都不静默吞。
    const paths = await selectDesktopPaths({
      filters: [{ name: fr.importHint, extensions: ['ics', 'ical', 'csv', 'md', 'txt', 'opml'] }],
      multiple: true,
      title: fr.importFiles
    })

    if (paths.length === 0) {
      return
    }

    setBusy('import')

    try {
      let landed = 0

      for (const path of paths) {
        const result = await importSourceFile(path)
        landed += result.created + result.updated
      }

      await reload()
      notify({ kind: 'success', message: fr.importDone.replace('{n}', String(landed)) })
    } catch (err) {
      notifyError(err, fr.importFailed)
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
    // 筛过之后**每组重新数一遍**：组头那一格条数说的是"这一段现在画了几行"，
    // 不是"这一段一共有几行"——画一个筛掉却仍写原数的组头就是一句假话。
    // `file` 那一类不再画信源行（用户 11-05：它没有采集频率，该是一颗导入按钮），
    // 但组要留着，否则那颗按钮没地方住。
    .map(group => ({
      ...group,
      rows: group.category === 'file' ? [] : group.rows.filter(row => keepsRow(row, tags))
    }))
    .filter(group => group.rows.length > 0 || group.category === 'custom' || group.category === 'file')

  // 只画翻得成人话的形状：没进目录的 `kind` 是机器词，不许直接上屏（屏幕文案纪律）。
  const chips = tagOptions(payload.sources).filter(kind => labels.tags[kind as keyof Labels['tags']])

  return (
    <SettingsContent>
      <SectionHeading
        icon={Layers3}
        meta={String(payload.sources.filter(row => row.category !== 'file').length)}
        title={labels.title}
      />

      {chips.length > 0
        ? (
            <div className="mt-2 flex flex-wrap gap-1" data-testid="source-tags">
              {chips.map(kind => (
                <button
                  aria-pressed={tags.includes(kind)}
                  className={`rounded-full border px-2 py-0.5 text-[length:var(--conversation-caption-font-size)] ${
                    tags.includes(kind)
                      ? 'border-(--ui-border-strong) bg-(--ui-bg-hover) text-(--ui-text-primary)'
                      : 'border-(--ui-border) text-(--ui-text-tertiary)'}`}
                  data-testid={`source-tag-${kind}`}
                  key={kind}
                  onClick={() => { $tags.set(toggleTag(tags, kind)) }}
                  type="button"
                >
                  {labels.tags[kind as keyof Labels['tags']]}
                </button>
              ))}
            </div>
          )
        : null}

      {groups.map(group => {
        // 异常只数后端三态里那一格（判据在 `sources_api.status_of`，这里不建第二条）。
        const errors = group.rows.filter(row => statusOf(row) === 'error').length
        const expanded = isOpen(fold, { category: group.category, errors })

        const meta = errors > 0
          ? `${group.rows.length} · ${labels.groups.errors.replace('{n}', String(errors))}`
          : group.category === 'file' ? '' : String(group.rows.length)

        return (
          <section className="mt-5 first:mt-0" data-testid={`source-group-${group.category}`} key={group.category}>
            {/* 组抬头照 `SettingsGroup` 那份统一外壳（11-02 拍）：12px 常规体 tertiary，
                不再是中黑体＋图标那一行。10-26 那条「缩进＋左边界」被用户 11-05 的统一令取代：
                行住进 `.dl-card`（8px squircle＋发丝边＋行间虚线），组与组靠卡分开，不靠缩进猜。 */}
            <button
              aria-expanded={expanded}
              aria-label={expanded ? labels.groups.collapse : labels.groups.expand}
              className="flex min-h-11 w-full items-end justify-between gap-3 px-1 text-left"
              data-testid={`source-group-${group.category}-toggle`}
              onClick={() => { decide(group.category, !expanded) }}
              type="button"
            >
              <span className="min-w-0 flex-1 truncate text-[length:var(--conversation-caption-font-size)] font-normal leading-[var(--dl-leading-row-desc)] text-(--ui-text-tertiary)">
                {labels.categories[group.category as keyof Labels['categories']] ?? group.category}
                {meta ? ` · ${meta}` : ''}
              </span>
              <DisclosureCaret className="size-3.5 shrink-0 text-(--ui-text-tertiary)" open={expanded} />
            </button>

            {expanded
              ? (
                  <div className="dl-card mt-2 px-4" data-testid={`source-group-${group.category}-rows`}>
                    <div className="flex flex-col">
                      {group.category === 'custom' ? <ExternalSources labels={labels} onChanged={reload} /> : null}

                      {group.category === 'file'
                        ? <ImportFilesRow busy={busy === 'import'} fr={fr} onImport={() => { void onImport() }} />
                        : group.rows.map(row => (
                            <SourceRowView
                              busy={busy === row.source}
                              fr={fr}
                              guiding={guide === row.source}
                              key={row.source}
                              labels={labels}
                              onActivate={() => {
                                setOpen(open === row.source ? null : row.source)
                                setGuide(null)
                              }}
                              onConfigure={() => {
                                setOpen(row.source)
                                setGuide(row.source)
                              }}
                              onReload={reload}
                              onToggle={onToggle}
                              onTry={onTry}
                              open={open === row.source}
                              row={row}
                            />
                          ))}
                    </div>
                  </div>
                )
              : null}
          </section>
        )
      })}
    </SettingsContent>
  )
}

function SourceRowView({
  busy,
  fr,
  guiding,
  labels,
  onActivate,
  onConfigure,
  onReload,
  onTry,
  onToggle,
  open,
  row
}: {
  busy: boolean
  fr: FirstRunLabels
  guiding: boolean
  labels: Labels
  onActivate: () => void
  onConfigure: () => void
  onReload: () => Promise<void>
  onTry: (row: SourceRow) => Promise<void>
  onToggle: (row: SourceRow, enabled: boolean) => Promise<void>
  open: boolean
  row: SourceRow
}) {
  const { t } = useI18n()
  const status = statusOf(row)

  // 名字两级：**语言目录**（我们自己那几条，四语各有写法）→ **后端那一格读数**
  // （声明名或站点自报的名，第三方包靠这一格零改动就有名字）。
  // 目录里那两条「教务（…）」已经删掉了——用户 10-24 的原话是「网站就显示网站名就行了，
  // 不要叫什么"教务"」：那种类目词是界面替一个网站起的名字，而它不该由界面来起。
  const displayName = t.agenda.sources[row.source as keyof typeof t.agenda.sources]
    ?? row.display_name ?? labels.unknownSource

  const checked = row.enabled ?? false
  const unconfigured = needsSetup(row)

  return (
    <ListRow
      action={
        // 行上**只有这一颗**，语法由 `ConnectionControl` 一处管（11-07 统一令）：
        // 没配过 = 加号（点它进引导）；配过 = 开关；坏了不换控件，旁边一颗红点、原因进 tooltip。
        <ConnectionControl
          busy={busy || row.enabled === null}
          checked={checked}
          configureLabel={fr.configure.replace('{name}', displayName)}
          configureTestId={`source-configure-${row.source}`}
          error={status === 'error'}
          errorTitle={status === 'error' ? statusReason(row, labels) : undefined}
          mode={unconfigured ? 'configure' : 'toggle'}
          onConfigure={onConfigure}
          onToggle={next => { void onToggle(row, next) }}
          toggleLabel={`${displayName} — ${labels[STATUS_KEY[status]]}`}
        />
      }
      below={open
        ? (
            <SourceDrawer
              busy={busy}
              guiding={guiding}
              labels={labels}
              onReload={onReload}
              onTry={onTry}
              row={row}
            />
          )
        : undefined}
      /* 行上**不写状态句子**（用户 11-07 拍「形状说话，坏了才红点」）：正常态只看控件，
         坏了在控件旁一颗红点、那句人话进 tooltip 与抽屉（裁定 45.4：码不上屏）。
         第三格是**这句"这是什么"**：信源自己声明的 summary（第三方包没填就整行不画，不许前端编）。 */
      description={row.summary ? <span className="truncate">{row.summary}</span> : undefined}
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
      {/* 通用那几格（用户 10-23：「只显示通用部分，包括上一次，下一次（显示多久之前，
          不要显示一个时间），频率」）——**绝对时刻不上屏**，人读的是"多久以前/以后"。 */}
      <dl className="grid gap-x-4 gap-y-1 text-[length:var(--conversation-caption-font-size)] sm:grid-cols-2">
        <Detail label={labels.drawerReason} value={statusReason(row, labels)} />
        <Detail
          label={labels.drawerLastRun}
          value={relativeLabel(row.last_run_at, labels) ?? labels.drawerNeverRun}
        />
        <Detail
          label={labels.drawerNextRun}
          value={row.enabled === false
            ? '—'
            : relativeLabel(row.next_run_at, labels) ?? labels.drawerNextEveryRound}
        />
        <Detail label={labels.drawerEvery} value={frequencyLabel(row.cadence, labels)} />
      </dl>

      {/* 池里/日程里两格留着，但压成一行小字：它们回答的是"这条采到东西没有"（裁 193 之后
          池才是落点），不是主视线里的东西。 */}
      <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
        {labels.drawerPoolCount}
        {' '}
        {row.pool_count === undefined || row.pool_count < 0
          ? labels.drawerPoolUnreadable
          : `${row.pool_count}${kinds.length > 0 ? `（${kinds.slice(0, 4).map(([kind, count]) => `${kind} ${count}`).join(' · ')}）` : ''}`}
        {' · '}
        {labels.drawerAgendaCount}
        {' '}
        {String(row.events)}
      </p>

      {/* 执行方逐字那一句留在这里，不进那一行：它是我们读给排障的人听的，不是给"这条源怎么了"
          那一格用的（用户 10-22：行上要人话、要产品化）。
          10-27 改的是**什么时候出现**：后端那一格现在除了出错态，也在"上一轮没成功而胶囊是绿的"
          时给旗子（微信那行 `not_listening` 被活性盖成已连接，屏幕上原先没地方说这句话）。
          屏上写的那一句按 `last_state` 查四语目录——目录里有就用人话，逐字原话只当兜底。 */}
      {row.status_detail
        ? (
            <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`source-run-note-${row.source}`}>
              {labels.drawerRunNote}
              {' — '}
              {(row.last_state ? labels.reasons[row.last_state] : undefined) ?? row.status_detail}
            </p>
          )
        : null}

      {/* 探针那句（只有"这条在本机还不成立"才有）：它是**读数**，逐字来自这条信源自己
          声明的那只探针，界面不转述。与上面那句同源同档——一个说"上一轮跑成什么样"，
          一个说"这台机器给不给得出它要的前提"。 */}
      {row.probe && !row.probe.ready && row.probe.detail
        ? (
            <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`source-probe-${row.source}`}>
              {labels.drawerProbe}
              {' — '}
              {row.probe.detail}
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

      {/* 按会话准入的那一条：每个群聊/私聊采不采，就在这一行下面定。
          名单与决定都由**这条信源自己声明的两只手**给（`SourceSpec.conversations` /
          `admission`），所以微信与钉钉看到的是同一个组件、同一条路由；第三方包只要同样
          声明那两手，这里一个字都不用改（判据 3）。画不画仍然由那句声明决定，面不按名字分支。
          用户 10-24：「把微信和钉钉的会话采集都做成做到UI里面去。这样太麻烦了」——
          日程板那座首启审查弹窗不在这里，它讲的是"要不要开始采"，不是"每扇采不采"。 */}
      {row.conversation_scoped
        ? (
            <div className="border-t border-(--ui-border-tertiary) pt-3" data-testid={`source-talkers-${row.source}`}>
              <ConversationList labels={labels} source={row.source} />
            </div>
          )
        : null}
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

/** 「上一次/下一次」那句人话。拿不到时刻就回 null（调用方决定写"还没跑过/跟着每一轮"）。 */
function relativeLabel(iso: string | undefined, labels: Labels): null | string {
  const rel = relativeTime(iso ?? '')

  if (!rel) {
    return null
  }

  if (rel.unit === 'just') {
    return labels.timeJustNow
  }

  if (rel.unit === 'minutes') {
    return (rel.past ? labels.timeMinutesAgo : labels.timeMinutesIn).replace('{n}', String(rel.minutes))
  }

  if (rel.unit === 'hours') {
    return (rel.past ? labels.timeHoursAgo : labels.timeHoursIn).replace('{n}', String(rel.hours))
  }

  return (rel.past ? labels.timeDaysAgo : labels.timeDaysIn).replace('{n}', String(rel.days))
}

/** 频率那一格的人话：把存量 cron 读回它本来停的那一档。自定义的才原样显示表达式。 */
function frequencyLabel(cadence: string | undefined, labels: Labels): string {
  const parsed = parseCadence(cadence ?? '')
  const clock = `${String(parsed.hour).padStart(2, '0')}:${String(parsed.minute).padStart(2, '0')}`

  switch (parsed.mode) {
    case 'every_round': {
      return labels.cadenceModeEveryRound
    }

    case 'every_days': {
      return labels.cadenceEveryDays.replace('{n}', String(parsed.days))
    }

    case 'every_hours': {
      return labels.cadenceEveryHours.replace('{n}', String(parsed.hours))
    }

    case 'every_minutes': {
      return labels.cadenceEveryMinutes.replace('{n}', String(parsed.minutes))
    }

    case 'daily': {
      return `${labels.cadenceModeDaily} ${clock}`
    }

    case 'weekly': {
      return `${labels.cadenceWeekdays[String(parsed.dow)] ?? ''} ${clock}`.trim()
    }

    default: {
      return parsed.raw
    }
  }
}

/** 开关之后那一句：只说"服务活没活"，四语各一句（后端那句原文只进控制台）。 */
function sidecarOutcomeLabel(listening: boolean, wanted: boolean, labels: Labels): string {
  if (wanted) {
    return listening ? labels.sidecarUp : labels.sidecarNotUp
  }

  return listening ? labels.sidecarNotDown : labels.sidecarDown
}

/**
 * Files 那一类不再是一条信源（用户 11-05：「毕竟没有采集频率……做成一个区分的导入按钮」）。
 * 它是一次用户主动的动作：选文件 → 进池。没有开关、没有频率、不进每一轮。
 */
function ImportFilesRow({ busy, fr, onImport }: {
  busy: boolean
  fr: FirstRunLabels
  onImport: () => void
}) {
  return (
    <div
      className="flex items-center justify-between gap-3 py-3"
      data-slot="settings-row"
      data-testid="source-import-row"
    >
      <div className="min-w-0">
        <p className="text-[length:var(--conversation-text-font-size)] text-(--ui-text-primary)">
          {fr.importFiles}
        </p>
        <p className="mt-0.5 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
          {fr.importHint}
        </p>
      </div>
      <Button disabled={busy} onClick={onImport} size="sm" variant="secondary">
        {fr.importFiles}
      </Button>
    </div>
  )
}
