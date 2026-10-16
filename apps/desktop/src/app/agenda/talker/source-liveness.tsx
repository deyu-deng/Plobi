/**
 * 信源活性那一横排（`WP-COLLECT-WIN` 的界面那半边）。
 *
 * 后端探针（`GET /api/collect/sources`）10-15 就在了，**全仓零消费方** ⇒ 「某条信源没在采」
 * 在界面上是静音的：用户看到的是一条空列表，不是「微信这条从没进过库」。这里把它说出来的规矩：
 * 句子全部来自语言目录（四语），**状态与时刻是读数**，原始 id 一律不上屏幕。
 */

import { useEffect, useState } from 'react'

import { PanelSectionLabel } from '@/app/overlays/panel'
import type { Translations } from '@/i18n'

import { getSourceLiveness } from './api'
import { buildSourceViews, type SourceLivenessPayload } from './liveness'

type Labels = Translations['agenda']['collect']['liveness']

export function SourceLiveness({ labels, names }: { labels: Labels; names: Record<string, string> }) {
  const [failed, setFailed] = useState(false)
  const [payload, setPayload] = useState<null | SourceLivenessPayload>(null)

  useEffect(() => {
    let cancelled = false

    // 取数走 `./api`（这个面板唯一的取数入口，测试整体 mock 它）。
    // 外面那层 async 是必需的：mock 出来的导出可能是 undefined，同步调用会**当场抛**，
    // 那会把整个面板炸成空白——读数拿不到就当这一横排没内容，不许连累别的。
    const run = async () => {
      try {
        const data = await getSourceLiveness()

        if (!cancelled && data) {
          setPayload(data)
        }
      } catch {
        if (!cancelled) {
          setFailed(true)
        }
      }
    }

    void run()

    return () => {
      cancelled = true
    }
  }, [])

  const views = buildSourceViews(payload, names)

  if (failed) {
    return <p className="px-1 text-[0.72rem] text-(--ui-text-tertiary)" data-testid="source-liveness-failed">{labels.failed}</p>
  }

  if (!payload || views.length === 0) {
    return null
  }

  return (
    <section className="space-y-1" data-testid="source-liveness">
      <PanelSectionLabel className="px-1">{labels.title}</PanelSectionLabel>
      <ul className="space-y-0.5">
        {views.map(view => (
          <li className="flex flex-wrap items-baseline gap-x-2 text-[0.72rem] text-(--ui-text-tertiary)" key={view.sourceId}>
            <span className="text-foreground/90">{view.displayName ?? labels.noName}</span>
            <span data-state={view.stateKey}>{labels[view.stateKey]}</span>
            {view.switchOff ? <span>{labels.switchOff}</span> : null}
            {view.stateKey === 'live' && view.ageDays !== null
              ? <span>{labels.ageDays(Math.max(0, Math.round(view.ageDays)))}</span>
              : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
