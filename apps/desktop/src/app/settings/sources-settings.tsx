/**
 * 设置 → 信源：分类下的一条条信源，每条一行（`ListRow`），配 §6.1 那张表里的动词。
 *
 * 三条纪律都落在这个文件里，改之前先读：
 *
 * 1. **读数不重算。** 状态与时刻逐字来自 `GET /api/collect/sources`；这一层不拿
 *    `age_days` 自己算一遍"几天前"（那是两份可能不一致的读数），不按 `state` 编新句子。
 * 2. **动词不在这儿定。** `./verbs` 那张表说了算，且表里没有的那一格**就画不出按钮**
 *    ——P6 那条「不许描述不存在的机制」在这里不是原则，是缺省行为。
 * 3. **主题随应用走。** 全程用 `--ui-text-*` / `text-foreground` 那一套 CSS 变量，
 *    没有一个字面色值（裁定：面不许硬编调色板）。
 */

import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Layers3, Play } from '@/lib/icons'
import { notify, notifyError } from '@/store/notifications'

import { ListRow, SectionHeading, SettingsContent } from './primitives'
import { getSources, setSourceEnabled, type SourceRow, type SourcesPayload, trySource } from './sources/api'
import { groupByCategory, verbFor } from './sources/verbs'

type Labels = ReturnType<typeof useI18n>['t']['settings']['sources']

export function SourcesSettings() {
  const { t } = useI18n()
  const labels = t.settings.sources

  const [payload, setPayload] = useState<null | SourcesPayload>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState<null | string>(null)

  const reload = useCallback(async () => {
    try {
      setPayload(await getSources())
      setLoadFailed(false)
    } catch {
      // 读不到就老实说读不到。画一个空面板等于说"一条信源都没有"——那是两句不同的话。
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
      // 逐字显示执行方自己那句话。这一层不转述、不总结（P6）。
      notify({ kind: result.ok ? 'success' : 'error', message: result.detail, title: labels.tryOnce })
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
        <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid="sources-failed">
          {labels.loadFailed}
        </p>
      </SettingsContent>
    )
  }

  if (!payload) {
    return (
      <SettingsContent>
        <SectionHeading icon={Layers3} title={labels.title} />
      </SettingsContent>
    )
  }

  const groups = groupByCategory(payload.sources, payload.categories)

  return (
    <SettingsContent>
      <SectionHeading icon={Layers3} title={labels.title} />
      <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{labels.lead}</p>

      {groups.map(group => (
        <section className="mt-5" data-testid={`source-group-${group.category}`} key={group.category}>
          <h3 className="text-[length:var(--conversation-caption-font-size)] font-medium text-(--ui-text-secondary)">
            {labels.categories[group.category as keyof Labels['categories']] ?? group.category}
          </h3>

          {group.rows.length === 0 ? (
            <p className="mt-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
              {labels.emptyCategory}
            </p>
          ) : (
            <div className="divide-y divide-(--ui-border-subtle)">
              {group.rows.map(row => (
                <SourceRowView busy={busy === row.source} key={row.source} labels={labels} onToggle={onToggle} onTry={onTry} row={row} />
              ))}
            </div>
          )}
        </section>
      ))}
    </SettingsContent>
  )
}

function SourceRowView({
  busy,
  labels,
  onTry,
  onToggle,
  row
}: {
  busy: boolean
  labels: Labels
  onTry: (row: SourceRow) => Promise<void>
  onToggle: (row: SourceRow, enabled: boolean) => Promise<void>
  row: SourceRow
}) {
  const { t } = useI18n()
  const verb = verbFor(row)

  return (
    <ListRow
      action={
        verb === 'try' ? (
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
        ) : null
      }
      description={<StatusSentence labels={labels} row={row} />}
      title={t.agenda.sources[row.source as keyof typeof t.agenda.sources] ?? labels.unknownSource}
    />
  )
}

/**
 * 那一行字。**每一句都来自语言目录或后端**：
 *
 * - 有 `note` 就用它（那是活性探针自己写的那句读数注记）；
 * - 开关读得到但关着，就在上面那句之外补一句「采集开关没点」；
 * - `has_builder === false` 额外把产品缺陷说出来——它是 bug 报告那一类，面只报不修。
 *
 * 这里**不按 `state` 编任何新句子**：状态词对应的解释全在语言目录里，挑不到就是没配，
 * 那该报没配，不该在组件里现编一句兜底。
 */
function StatusSentence({ labels, row }: { labels: Labels; row: SourceRow }) {
  const pieces: string[] = [row.note || labels.stateUnknown]

  if (row.enabled === false) {
    pieces.push(labels.switchOff)
  } else if (row.enabled === null) {
    pieces.push(labels.switchUnreadable)
  }

  if (row.has_builder === false && row.enabled === true) {
    pieces.push(labels.noBuilder)
  }

  if (row.state === 'live' && row.age_days !== null) {
    pieces.push(labels.lastWrite(Math.max(0, Math.round(row.age_days))))
  }

  return (
    <span className="flex flex-col gap-0.5" data-state={row.state}>
      {pieces.map(piece => <span key={piece}>{piece}</span>)}
    </span>
  )
}