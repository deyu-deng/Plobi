import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { openMonitorFace } from '@/store/layout'

/**
 * 设置里「记忆」那一页的正文（用户 2026-10-30：入口放设置里、只做个人记忆、
 * 删除真删并记一笔）。
 *
 * 三份东西：全局记忆（GLOBAL）／用户画像（USER 精简版）／这个 Agent 的记忆。
 * 删除直接调 `POST /api/memory/forget`，后端删掉那一条并写一行回执；
 * 这一页不做脑里正典的浏览——那是知识库那一屏的事。
 *
 * **一份一行、条目进抽屉**（用户 11-19 21:2x：「这些记忆具体内容应该放到抽屉里面。
 * 这样展开太丑了」）：原来三份各自带边框、各自把条目全摊在行上，长的能占两屏，
 * 把下面十四个开关一路顶下去。现在行上只剩名字与读数（已用/上限 · 几条），
 * 点开右边抽屉才逐条读、逐条忘。
 *
 * **四个名字由前端给**（用户同一句里的「有些语言没有产品化」）：`memory_api.py`
 * 那四句标签是写死的中文，切到英/日就只剩它们还是中文。这里按 `key` 译，
 * 认不出的 key 才回落后端那个 `label`。
 *
 * **只用陈述句、不写解释**（用户 11-19 22:2x：「有些『这个 Agent 的记忆』叫法太口语化了」
 * 「一些元注释也不需要上屏幕。保持简洁优雅」）：归属由名字承担（`X 的记忆`），
 * 不再单起一行「这几份记忆属于 X」，也不在抽屉抬头补「只有 X 会读这一份」；
 * 读数是读数，动作是动作，不夹带说明。
 *
 * 四语文案放在这里而不是加进 `i18n/{types,en,zh,ja,zh-hant}.ts`：那五档此刻由五条
 * 在派线持有（认领门按整档路径挡），而"新屏要四语齐全"这条不能因此不做。
 * 等那五档空出来整体搬进中心表。
 */

export interface MemoryEntry {
  index: number
  text: string
}

export interface MemoryScope {
  key: string
  label: string
  present: boolean
  chars: number
  limit: number
  entries: MemoryEntry[]
  streak?: number
  threshold?: number
}

interface LedgersPayload {
  agent: string
  scopes: MemoryScope[]
}

/* ——— 写入边界（WP-MIND-SILENCE-VISIBLE）：GET /api/memory/boundary ———
 *
 * 两个连续采纳数是两件事，别读成一个进度：
 * - 上面那三份记忆的 `streak` 来自 `TrustLedger`（L0：我汇报给你，你信不信，门槛 8）；
 * - 这里每类的 `streak` 来自 `consent.db`（L1：这类写入能不能不问就写，门槛 5）。
 * 它们各自的 `threshold` 由后端给，前端不硬编码任何一个数。
 */

export interface BoundaryCategory {
  category: string
  adopted: number
  rejected: number
  edited: number
  streak: number
  streak_source: string
  last_ts: number
  recent: string[]
}

export interface BoundaryPolicy {
  key: string
  category: string
  text: string
  state: string
  evidence: string[]
}

export interface BoundaryTrace {
  id: string
  ts: number
  category: string
  target: string
  silent: boolean
  reason: string
  trace: { policy_keys?: string[] } & Record<string, unknown>
}

export interface BoundaryPayload {
  categories: Record<string, BoundaryCategory>
  breaker: { tripped: boolean; count: number; threshold: number; window_days: number }
  policies: BoundaryPolicy[]
  traces: BoundaryTrace[]
  thresholds: { n_adopt: number; k_reject: number; window_days: number }
  canon: string
  db: string
}

/**
 * 这一屏的四语文案。**只放屏上真出现的字**：名字、读数、动作、空态、错误。
 * 不写解释句（「这几份记忆属于 X」「只有 X 会读这一份」那类），
 * 名称本身已经说清归属；多一句只是给人多读一行。
 */
interface LedgerCopy {
  /**
   * 不属于某一个 Agent 的三份。**取代后端给的 `label`**（`memory_api.py` 那几句是写死的中文）。
   * `status` 是一份汇总：每家写自己那一小段，拼成「各首席近况摘要」，
   * 只注入指挥（用户 11-19 22:39 定的名字与归属）。
   */
  scopeFixed: { global: string; user: string; status: string }
  /** 属于某一个 Agent 的那一份：名字带那个 Agent 的名字。 */
  scopeOwned: { memory: (owner: string) => string }
  /** 后端报 `l1`（没绑某个 Agent）时，那两份挂在指挥名下。 */
  conductor: string
  /** 行右读数：已用/上限 · 几条。 */
  scopeMeta: (chars: number, limit: number, count: number) => string
  openAria: (label: string) => string
  openPending: string
  empty: string
  forget: string
  confirmForget: string
  forgetHint: string
  failed: string
  unavailable: string
  needsRestart: string
  /** 汇报采纳（L0）：我汇报了几次、你采纳了几次。 */
  streakLabel: (streak: number, threshold: number) => string
  // ——— 写入边界（WP-MIND-SILENCE-VISIBLE）———
  boundaryTitle: string
  boundaryRule: (n: number, k: number, days: number) => string
  breakerAlert: (count: number, k: number, days: number) => string
  writeStreak: (streak: number, n: number) => string
  rejectedTimes: (n: number) => string
  granted: string
  notGranted: string
  policiesTitle: string
  policyRevoke: string
  tracesTitle: string
  tracesEmpty: string
  traceSilent: (target: string, category: string, key: string) => string
  traceAsked: (target: string, category: string, why: string) => string
  fiveRules: string
  boundaryEmpty: string
}

const COPY: Record<'en' | 'zh' | 'ja' | 'zh-hant', LedgerCopy> = {
  en: {
    scopeFixed: { global: 'Global memory', status: 'Principal status digest', user: 'User profile' },
    scopeOwned: { memory: owner => `${owner}'s memory` },
    conductor: 'Conductor',
    scopeMeta: (chars, limit, count) => `${chars}/${limit} · ${count} ${count === 1 ? 'entry' : 'entries'}`,
    openAria: label => `Open ${label}`,
    openPending: 'Pending',
    empty: 'No entries yet',
    forget: 'Delete',
    confirmForget: 'Confirm delete',
    forgetHint: 'Delete this entry and write a receipt',
    failed: 'Delete failed',
    unavailable: 'Memory is unavailable',
    needsRestart: 'Backend is older than this page — restart Plobi to load it.',
    streakLabel: (streak, threshold) => `Reports accepted ${streak}/${threshold}`,
    boundaryTitle: 'Write permissions',
    boundaryRule: (n, k, days) =>
      `Silent writes after ${n} approvals in a row · everything pauses on ${k} rejections in ${days} days`,
    breakerAlert: (count, k, days) =>
      `All paused: ${count}/${k} rejections in ${days} days. Every class is back to asking.`,
    writeStreak: (streak, n) => `${streak}/${n} in a row`,
    rejectedTimes: n => `${n} rejected`,
    granted: 'granted',
    notGranted: 'not granted',
    policiesTitle: 'Active permissions',
    policyRevoke: 'Delete this line to ask again',
    tracesTitle: 'Silent writes',
    tracesEmpty: 'None yet',
    traceSilent: (target, category, key) => `Wrote ${target} (${category} · ${key})`,
    traceAsked: (target, category, why) => `${target} (${category}) asked: ${why}`,
    fiveRules: 'the five rules',
    boundaryEmpty: 'No data yet. Approve or reject a few times and the numbers appear here.'
  },
  zh: {
    scopeFixed: { global: '全局记忆', status: '各首席近况摘要', user: '用户画像' },
    scopeOwned: { memory: owner => `${owner} 的记忆` },
    conductor: '指挥',
    scopeMeta: (chars, limit, count) => `${chars}/${limit} · ${count} 条`,
    openAria: label => `展开 ${label}`,
    openPending: '待批',
    empty: '暂无条目',
    forget: '删除',
    confirmForget: '确认删除',
    forgetHint: '删除该条目，并写入回执',
    failed: '删除失败',
    unavailable: '无法读取记忆',
    needsRestart: '后端版本低于此页面，重启 Plobi 后生效。',
    streakLabel: (streak, threshold) => `汇报采纳 ${streak}/${threshold}`,
    boundaryTitle: '写入授权',
    boundaryRule: (n, k, days) => `连续采纳 ${n} 次后自动写入 · ${days} 天内驳回 ${k} 次则全部暂停`,
    breakerAlert: (count, k, days) => `已全部暂停：${days} 天内驳回 ${count}/${k} 次。各类别恢复为每次询问。`,
    writeStreak: (streak, n) => `连续 ${streak}/${n}`,
    rejectedTimes: n => `驳回 ${n} 次`,
    granted: '已授权',
    notGranted: '未授权',
    policiesTitle: '生效中的授权',
    policyRevoke: '删除此行后恢复为每次询问',
    tracesTitle: '静默写入',
    tracesEmpty: '暂无记录',
    traceSilent: (target, category, key) => `已写入 ${target}（${category} · ${key}）`,
    traceAsked: (target, category, why) => `${target}（${category}）已询问：${why}`,
    fiveRules: '五条判据',
    boundaryEmpty: '暂无数据。批准或驳回若干次后显示统计。'
  },
  ja: {
    scopeFixed: { global: 'グローバル記憶', status: '首席の近況まとめ', user: 'ユーザープロフィール' },
    scopeOwned: { memory: owner => `${owner} の記憶` },
    conductor: '指揮者',
    scopeMeta: (chars, limit, count) => `${chars}/${limit} · ${count} 件`,
    openAria: label => `${label} を開く`,
    openPending: '承認待ち',
    empty: '項目はまだありません',
    forget: '削除',
    confirmForget: '削除の確認',
    forgetHint: 'この項目を削除し、記録を残します',
    failed: '削除できませんでした',
    unavailable: '記憶を読み込めません',
    needsRestart: 'バックエンドが古いため、Plobi を再起動してください。',
    streakLabel: (streak, threshold) => `報告の承認 ${streak}/${threshold}`,
    boundaryTitle: '書き込みの許可',
    boundaryRule: (n, k, days) => `${n} 回連続で自動書き込み · ${days} 日以内に ${k} 回却下で全停止`,
    breakerAlert: (count, k, days) =>
      `全停止：${days} 日以内に ${count}/${k} 回却下。全カテゴリが毎回確認に戻りました。`,
    writeStreak: (streak, n) => `連続 ${streak}/${n}`,
    rejectedTimes: n => `却下 ${n} 回`,
    granted: '許可済み',
    notGranted: '未許可',
    policiesTitle: '有効な許可',
    policyRevoke: 'この行を削除すると毎回確認に戻ります',
    tracesTitle: '無確認の書き込み',
    tracesEmpty: 'まだありません',
    traceSilent: (target, category, key) => `${target} に書き込み（${category} · ${key}）`,
    traceAsked: (target, category, why) => `${target}（${category}）は確認：${why}`,
    fiveRules: '五つの規則',
    boundaryEmpty: 'データはまだありません。数回承認・却下すると統計が表示されます。'
  },
  'zh-hant': {
    scopeFixed: { global: '全域記憶', status: '各首席近況摘要', user: '使用者畫像' },
    scopeOwned: { memory: owner => `${owner} 的記憶` },
    conductor: '指揮',
    scopeMeta: (chars, limit, count) => `${chars}/${limit} · ${count} 條`,
    openAria: label => `展開 ${label}`,
    openPending: '待批',
    empty: '暫無條目',
    forget: '刪除',
    confirmForget: '確認刪除',
    forgetHint: '刪除該條目，並寫入回執',
    failed: '刪除失敗',
    unavailable: '無法讀取記憶',
    needsRestart: '後端版本低於此頁面，重啟 Plobi 後生效。',
    streakLabel: (streak, threshold) => `匯報採納 ${streak}/${threshold}`,
    boundaryTitle: '寫入授權',
    boundaryRule: (n, k, days) => `連續採納 ${n} 次後自動寫入 · ${days} 天內駁回 ${k} 次則全部暫停`,
    breakerAlert: (count, k, days) => `已全部暫停：${days} 天內駁回 ${count}/${k} 次。各類別恢復為每次詢問。`,
    writeStreak: (streak, n) => `連續 ${streak}/${n}`,
    rejectedTimes: n => `駁回 ${n} 次`,
    granted: '已授權',
    notGranted: '未授權',
    policiesTitle: '生效中的授權',
    policyRevoke: '刪除此行後恢復為每次詢問',
    tracesTitle: '靜默寫入',
    tracesEmpty: '暫無記錄',
    traceSilent: (target, category, key) => `已寫入 ${target}（${category} · ${key}）`,
    traceAsked: (target, category, why) => `${target}（${category}）已詢問：${why}`,
    fiveRules: '五條判據',
    boundaryEmpty: '暫無數據。批准或駁回若干次後顯示統計。'
  }
}

function copyFor(locale: string): LedgerCopy {
  const value = (locale || 'en').toLowerCase()

  if (value.startsWith('zh-hant') || value.includes('hant')) {
    return COPY['zh-hant']
  }

  if (value.startsWith('zh')) {
    return COPY.zh
  }

  if (value.startsWith('ja')) {
    return COPY.ja
  }

  return COPY.en
}

export function MemoryLedgers() {
  const { locale } = useI18n()
  const m = copyFor(String(locale))
  const [data, setData] = useState<LedgersPayload | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 抽屉里那一份（null = 关着）。条目不再铺在行上：三份加起来能有几十条，
  // 全摊开就把下面十四个开关一路顶到屏外，读起来也没有「这是同一份里的几条」这个层次。
  const [openScope, setOpenScope] = useState<string | null>(null)
  // 两下才删：真删不可逆，误点一次不该丢一条。第一下只把这一颗变成「确认删除」。
  const [armed, setArmed] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const payload = await window.plobiDesktop.api<LedgersPayload>({ path: '/api/memory/ledgers' })
      setData(payload)
      setError(null)
    } catch (err) {
      // 404 通常意味着"界面是新的、后端还是旧进程"（重启前就是这样）——那句话比"读不到"有用。
      const text = String(err)
      setError(text.includes('404') ? m.needsRestart : m.unavailable)
    }
  }, [m.needsRestart, m.unavailable])

  useEffect(() => {
    void load()
  }, [load])

  const forget = async (scope: string, index: number) => {
    setBusy(`${scope}:${index}`)

    try {
      const payload = await window.plobiDesktop.api<LedgersPayload & { ok?: boolean }>({
        body: { index, scope },
        method: 'POST',
        path: '/api/memory/forget'
      })

      setData({ agent: payload.agent, scopes: payload.scopes })
      // 后端那几个失败串是中文（`memory_api.py` 的 `_forget`）——不往屏幕上搬，
      // 否则一切到英/日就漏出中文。出了什么事以「删除失败」为准，明细在回执里。
      setError(payload.ok === false ? m.failed : null)
    } catch {
      setError(m.failed)
    } finally {
      setBusy(null)
    }
  }

  const scopes = data?.scopes ?? []
  const active = scopes.find(scope => scope.key === openScope) ?? null
  // 这两份记忆属于哪一个 Agent：后端 `_snapshot()` 给的是它解析出来的那一个
  // （`resolve_agent_slug()`）。`l1` 表示没绑到具体 Agent —— 那两份就挂在指挥名下，
  // 而不是留着「这个 Agent」让人猜。
  const owner = data?.agent && data.agent !== 'l1' ? data.agent : m.conductor

  const labelOf = (scope: MemoryScope) => {
    if (scope.key === 'memory') {
      return m.scopeOwned.memory(owner)
    }

    if (scope.key === 'global' || scope.key === 'user' || scope.key === 'status') {
      return m.scopeFixed[scope.key]
    }

    return scope.label
  }

  const close = () => {
    setOpenScope(null)
    setArmed(null)
  }

  return (
    <>
      <div className="mt-3 flex flex-col gap-1" data-testid="memory-ledgers">
        <div className="flex items-center justify-end">
          <Button onClick={() => openMonitorFace('mind-pending')} size="sm" variant="ghost">
            {m.openPending}
          </Button>
        </div>

        {error ? (
          <div className="text-destructive text-xs" role="alert">
            {error}
          </div>
        ) : null}

        {/* 一份一行：名字 + 读数 + 一个尖角。条目本身在抽屉里（点开那一行才看）。 */}
        {scopes.map((scope, index) => (
          <div
            className={cn('border-border/60', index > 0 && 'border-t')}
            data-testid={`memory-scope-${scope.key}`}
            key={scope.key}
          >
            <button
              aria-label={m.openAria(labelOf(scope))}
              className="row-hover flex w-full items-center gap-3 rounded-md px-1 py-2 text-left"
              onClick={() => {
                setArmed(null)
                setOpenScope(scope.key)
              }}
              type="button"
            >
              <span className="min-w-0 flex-1 truncate text-[0.8rem] text-foreground">{labelOf(scope)}</span>
              <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                {m.scopeMeta(scope.chars, scope.limit, scope.entries.length)}
              </span>
              <Codicon className="shrink-0 text-(--ui-text-tertiary)" name="chevron-right" size="0.8125rem" />
            </button>
          </div>
        ))}
      </div>

      {/* 抽屉：一份记忆的全部条目。删除仍是两下——第一下只上膛，第二下才真删。 */}
      {/* `belowTitlebar`：抽屉底色铺满全高，但内容与关闭按钮从窗口顶栏下面开始，
            否则标题正好压在系统的最小化/最大化/关闭上（用户 11-19 21:5x 指出的那一处）。 */}
      <Sheet
        onOpenChange={next => {
          if (!next) {
            close()
          }
        }}
        open={Boolean(active)}
      >
        <SheetContent belowTitlebar className="sm:max-w-md" side="right">
          {active ? (
            <>
              <SheetHeader>
                <SheetTitle>{labelOf(active)}</SheetTitle>
                <SheetDescription>
                  <span className="tabular-nums">{m.scopeMeta(active.chars, active.limit, active.entries.length)}</span>
                  {active.key === 'user' || active.key === 'status' ? null : (
                    <>
                      {' · '}
                      <span>{m.streakLabel(active.streak ?? 0, active.threshold ?? 8)}</span>
                    </>
                  )}
                </SheetDescription>
              </SheetHeader>

              <div className="min-h-0 flex-1 overflow-y-auto px-3" data-testid="memory-drawer">
                {error ? (
                  <div className="text-destructive mb-2 text-xs" role="alert">
                    {error}
                  </div>
                ) : null}

                {active.entries.length === 0 ? (
                  <p className="text-muted-foreground text-xs">{m.empty}</p>
                ) : (
                  <ul className="flex flex-col gap-3">
                    {active.entries.map(entry => {
                      // 近况摘要是「一家一段」：第一行是那家的名字，下面是它自己写的几行。
                      const readonly = active.key === 'status'
                      const [head, ...rest] = readonly ? entry.text.split('\n') : [entry.text]

                      return (
                        <li className="flex items-start justify-between gap-3 text-[0.8rem]" key={entry.index}>
                          {rest.length > 0 ? (
                            <span className="min-w-0 flex-1">
                              <span className="text-muted-foreground block text-xs">{head}</span>
                              <span className="block whitespace-pre-wrap break-words">{rest.join('\n').trim()}</span>
                            </span>
                          ) : (
                            <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{head}</span>
                          )}
                          {readonly ? null : (
                            <Button
                              aria-label={m.forget}
                              disabled={busy === `${active.key}:${entry.index}`}
                              onClick={() => {
                                if (armed !== entry.index) {
                                  setArmed(entry.index)

                                  return
                                }

                                setArmed(null)
                                void forget(active.key, entry.index)
                              }}
                              size="sm"
                              title={m.forgetHint}
                              variant="ghost"
                            >
                              {armed === entry.index ? m.confirmForget : m.forget}
                            </Button>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  )
}

/**
 * 「凭什么没问你」——边界层的读数（`GET /api/memory/boundary`，只读）。
 *
 * 四块，按「出事时最想知道哪个」排序：
 * ① 熔断（全部暂停，没熔断就不占地方）；
 * ② 每类一行（连续 X/5、窗口内驳回、授没授权）——答「这条为什么还在问我」；
 * ③ 已生效政策的**原文** ＋ 怎么收回（删掉那一行）——这是唯一能反悔的地方；
 * ④ 最近几次静默，每行拼成一句人读的话。
 *
 * 老后端（这个口还不存在）整块不渲染：与其报一个「读不到」，不如让页面跟以前一样。
 *
 * **挂载点在 `memory.mind_write_auto_approve` 那一行下面**（`config-settings.tsx` 的
 * `descriptionExtra`，用户 11-17 08:5x 拍）。它原来嵌在 :func:`MemoryLedgers` 末尾、
 * 跟着 `memory.provider` 那行走，要往下滚十二行才见到——「接哪个外部脑」的下拉跟
 * 「这一类为什么要问你」本来也不是一件事。
 */
export function MemoryBoundary() {
  const { locale } = useI18n()
  const m = copyFor(String(locale))
  const [data, setData] = useState<BoundaryPayload | null>(null)
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    let alive = true

    const load = async () => {
      try {
        const payload = await window.plobiDesktop.api<BoundaryPayload>({ path: '/api/memory/boundary' })

        if (alive) {
          setData(payload)
          setMissing(false)
        }
      } catch {
        if (alive) {
          setMissing(true)
        }
      }
    }

    void load()

    return () => {
      alive = false
    }
  }, [])

  if (missing || !data) {
    return null
  }

  const categories = Object.values(data.categories ?? {}).sort((a, b) => a.category.localeCompare(b.category))
  const active = (data.policies ?? []).filter(policy => policy.state === 'active')
  const traces = (data.traces ?? []).slice(0, 3)
  const granted = new Set(active.map(policy => policy.category))

  return (
    <section className="border-border rounded-lg border p-3" data-testid="memory-boundary">
      <header className="flex items-baseline justify-between gap-3">
        <h4 className="text-sm font-medium">{m.boundaryTitle}</h4>
        <span className="text-muted-foreground text-xs">
          {m.boundaryRule(data.thresholds.n_adopt, data.thresholds.k_reject, data.thresholds.window_days)}
        </span>
      </header>

      {data.breaker.tripped ? (
        <div className="text-destructive mt-2 text-xs" role="alert">
          {m.breakerAlert(data.breaker.count, data.breaker.threshold, data.breaker.window_days)}
        </div>
      ) : null}

      {categories.length === 0 ? (
        <p className="text-muted-foreground mt-2 text-xs">{m.boundaryEmpty}</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {categories.map(item => (
            <li className="flex items-baseline justify-between gap-3 text-[0.8rem]" key={item.category}>
              <span className="min-w-0 flex-1 truncate">{item.category}</span>
              <span className="text-muted-foreground flex items-center gap-2 text-xs tabular-nums">
                <span>{m.writeStreak(item.streak, data.thresholds.n_adopt)}</span>
                {item.rejected > 0 ? <span>{m.rejectedTimes(item.rejected)}</span> : null}
                <span>{granted.has(item.category) ? m.granted : m.notGranted}</span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {active.length > 0 ? (
        <div className="mt-3">
          <h5 className="text-xs font-medium">{m.policiesTitle}</h5>
          <ul className="mt-1 flex flex-col gap-2">
            {active.map(policy => (
              <li className="text-[0.8rem]" key={policy.key}>
                <span className="break-words">{policy.text}</span>
                <span className="text-muted-foreground mt-0.5 block text-xs">{m.policyRevoke}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-3">
        <h5 className="text-xs font-medium">{m.tracesTitle}</h5>
        {traces.length === 0 ? (
          <p className="text-muted-foreground mt-1 text-xs">{m.tracesEmpty}</p>
        ) : (
          <ul className="mt-1 flex flex-col gap-1">
            {traces.map(trace => (
              <li className="text-muted-foreground text-xs break-words" key={trace.id}>
                {trace.silent
                  ? m.traceSilent(trace.target, trace.category, trace.trace?.policy_keys?.[0] || m.fiveRules)
                  : m.traceAsked(trace.target, trace.category, trace.reason)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
