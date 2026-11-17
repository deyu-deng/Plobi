import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { setMindPendingOpen } from '@/store/mind-pending'

/**
 * 设置里「记忆」那一页的正文（用户 2026-10-30：入口放设置里、只做个人记忆、
 * 「忘掉」真删并记一笔）。
 *
 * 三段东西：共用纪律（GLOBAL）／你的画像（USER 精简版）／这个 Agent 自己的记忆。
 * 「忘掉」直接调 `POST /api/memory/forget`，后端删掉那一条并写一行回执；
 * 这一页不做脑里正典的浏览——那是知识库那一屏的事。
 *
 * 四语文案放在这里而不是加进 `i18n/{types,en,zh,ja,zh-hant}.ts`：那五档此刻由别线在派持有
 * （认领门按整档路径挡），而"新屏要四语齐全"这条不能因此不做。等那五档空出来整体搬进中心表。
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
 * 两个 "连采" 是两件事，别读成一个进度：
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

interface LedgerCopy {
  agentLine: (agent: string) => string
  openPending: string
  empty: string
  forget: string
  forgetHint: string
  failed: string
  unavailable: string
  unbound: string
  streakLabel: (streak: number, threshold: number) => string
  confirmForget: string
  needsRestart: string
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
    agentLine: agent => `Agent: ${agent}`,
    streakLabel: (streak, threshold) => `${streak}/${threshold} nods (report)`,
    confirmForget: 'Confirm forget',
    needsRestart: 'Backend is older than this page — restart Plobi to load it.',
    unbound: 'Secretary — no agent bound',
    openPending: 'Review pending',
    empty: 'Nothing remembered yet',
    forget: 'Forget',
    forgetHint: 'Delete this entry and leave a receipt',
    failed: 'Not deleted',
    unavailable: 'Memory is not readable right now',
    boundaryTitle: 'Write boundary',
    boundaryRule: (n, k, days) => `${n} in a row earns it · ${k} rejections in ${days} days stops everything`,
    breakerAlert: (count, k, days) =>
      `All paused: ${count}/${k} rejections in ${days} days — every class goes back to asking until you look`,
    writeStreak: (streak, n) => `${streak}/${n} nods (write)`,
    rejectedTimes: n => `${n} rejected`,
    granted: 'granted',
    notGranted: 'not granted',
    policiesTitle: 'Standing permissions',
    policyRevoke: 'Delete this line and it asks again',
    tracesTitle: 'Times it did not ask',
    tracesEmpty: 'Nothing has been silent yet',
    traceSilent: (target, category, key) => `wrote ${target} (${category}; by ${key})`,
    traceAsked: (target, category, why) => `${target} (${category}) still asked: ${why}`,
    fiveRules: 'the five rules',
    boundaryEmpty: 'Nothing to learn from yet — approve or reject a few times and the numbers appear here'
  },
  zh: {
    agentLine: agent => `这份 Agent：${agent}`,
    streakLabel: (streak, threshold) => `连采 ${streak}/${threshold}（汇报）`,
    confirmForget: '确认忘掉',
    needsRestart: '后端还是旧进程，重启 Plobi 后这一页就能读。',
    unbound: '总秘书（没有绑定到某个 Agent）',
    openPending: '看等你点头的',
    empty: '还没记住什么',
    forget: '忘掉',
    forgetHint: '删掉这一条，并留一行回执',
    failed: '没删成',
    unavailable: '现在读不到记忆',
    boundaryTitle: '写入边界',
    boundaryRule: (n, k, days) => `连采 ${n} 次才够格 · ${days} 天内驳回 ${k} 次就全停`,
    breakerAlert: (count, k, days) =>
      `全部暂停：${days} 天内驳回 ${count}/${k} 次 ⇒ 全交回人，等你翻过一遍再说`,
    writeStreak: (streak, n) => `连采 ${streak}/${n}（写入）`,
    rejectedTimes: n => `驳回 ${n}`,
    granted: '已授权',
    notGranted: '未授权',
    policiesTitle: '已生效的授权',
    policyRevoke: '删掉这一行，这一类就回到每次都问',
    tracesTitle: '最近几次没问你',
    tracesEmpty: '还没有静默过',
    traceSilent: (target, category, key) => `写进 ${target}（${category} 类；凭 ${key}）`,
    traceAsked: (target, category, why) => `${target}（${category} 类）还是问了你：${why}`,
    fiveRules: '五条判据',
    boundaryEmpty: '还没有值得学的——批准或驳回几次之后，这里的读数才会长出来'
  },
  ja: {
    agentLine: agent => `この Agent：${agent}`,
    streakLabel: (streak, threshold) => `連続 ${streak}/${threshold}（報告）`,
    confirmForget: '忘れる（確認）',
    needsRestart: 'バックエンドが古いままです。Plobi を再起動してください。',
    unbound: '秘書（エージェント未割当）',
    openPending: '承認待ちを見る',
    empty: 'まだ何も覚えていません',
    forget: '忘れる',
    forgetHint: 'この項目を削除し、記録を残します',
    failed: '削除できませんでした',
    unavailable: '今は記憶を読めません',
    boundaryTitle: '書き込みの境界',
    boundaryRule: (n, k, days) => `連続 ${n} 回で資格 · ${days} 日以内に ${k} 回却下で全部停止`,
    breakerAlert: (count, k, days) =>
      `全停止中：${days} 日以内に ${count}/${k} 回却下 ⇒ すべて確認に戻りました`,
    writeStreak: (streak, n) => `連続 ${streak}/${n}（書き込み）`,
    rejectedTimes: n => `却下 ${n}`,
    granted: '許可済み',
    notGranted: '未許可',
    policiesTitle: '有効な許可',
    policyRevoke: 'この行を消すと毎回確認に戻ります',
    tracesTitle: '確認しなかった回',
    tracesEmpty: 'まだ静かに書いたことはありません',
    traceSilent: (target, category, key) => `${target} に書き込み（${category}；${key} による）`,
    traceAsked: (target, category, why) => `${target}（${category}）は確認しました：${why}`,
    fiveRules: '五つの規則',
    boundaryEmpty: 'まだ学ぶ材料がありません——数回承認・却下するとここに数字が出ます'
  },
  'zh-hant': {
    agentLine: agent => `這份 Agent：${agent}`,
    streakLabel: (streak, threshold) => `連採 ${streak}/${threshold}（匯報）`,
    confirmForget: '確認忘掉',
    needsRestart: '後端還是舊進程，重啟 Plobi 後這一頁就能讀。',
    unbound: '總秘書（沒有綁定到某個 Agent）',
    openPending: '看等你點頭的',
    empty: '還沒記住什麼',
    forget: '忘掉',
    forgetHint: '刪掉這一條，並留一行回執',
    failed: '沒刪成',
    unavailable: '現在讀不到記憶',
    boundaryTitle: '寫入邊界',
    boundaryRule: (n, k, days) => `連採 ${n} 次才夠格 · ${days} 天內駁回 ${k} 次就全停`,
    breakerAlert: (count, k, days) =>
      `全部暫停：${days} 天內駁回 ${count}/${k} 次 ⇒ 全交回人，等你翻過一遍再說`,
    writeStreak: (streak, n) => `連採 ${streak}/${n}（寫入）`,
    rejectedTimes: n => `駁回 ${n}`,
    granted: '已授權',
    notGranted: '未授權',
    policiesTitle: '已生效的授權',
    policyRevoke: '刪掉這一行，這一類就回到每次都問',
    tracesTitle: '最近幾次沒問你',
    tracesEmpty: '還沒有靜默過',
    traceSilent: (target, category, key) => `寫進 ${target}（${category} 類；憑 ${key}）`,
    traceAsked: (target, category, why) => `${target}（${category} 類）還是問了你：${why}`,
    fiveRules: '五條判據',
    boundaryEmpty: '還沒有值得學的——批准或駁回幾次之後，這裡的讀數才會長出來'
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
  // 两下才删：真删不可逆，误点一次不该丢一条。第一下只把这一颗变成「确认忘掉」。
  const [armed, setArmed] = useState<string | null>(null)

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
      const payload = await window.plobiDesktop.api<LedgersPayload & { ok?: boolean; error?: string }>({
        body: { index, scope },
        method: 'POST',
        path: '/api/memory/forget'
      })
      setData({ agent: payload.agent, scopes: payload.scopes })
      setError(payload.ok === false ? payload.error || m.failed : null)
    } catch {
      setError(m.failed)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mt-3 flex flex-col gap-4" data-testid="memory-ledgers">
      <div className="flex items-center justify-between gap-3">
        <div className="text-muted-foreground text-xs">{data ? (data.agent && data.agent !== 'l1' ? m.agentLine(data.agent) : m.unbound) : null}</div>
        <Button onClick={() => setMindPendingOpen(true)} size="sm" variant="ghost">
          {m.openPending}
        </Button>
      </div>

      {error ? (
        <div className="text-destructive text-xs" role="alert">
          {error}
        </div>
      ) : null}

      {(data?.scopes ?? []).map(scope => (
        <section className="border-border rounded-lg border p-3" key={scope.key}>
          <header className="flex items-baseline justify-between gap-3">
            <h4 className="text-sm font-medium">{scope.label}</h4>
            <span className="text-muted-foreground flex items-center gap-2 text-xs tabular-nums">
              {scope.key === 'user' ? null : (
                <span title={m.forgetHint}>{m.streakLabel(scope.streak ?? 0, scope.threshold ?? 8)}</span>
              )}
              <span>
                {scope.chars}/{scope.limit}
              </span>
            </span>
          </header>

          {scope.entries.length === 0 ? (
            <p className="text-muted-foreground mt-2 text-xs">{m.empty}</p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {scope.entries.map(entry => (
                <li className="flex items-start justify-between gap-3 text-[0.8rem]" key={entry.index}>
                  <span className="min-w-0 flex-1 break-words whitespace-pre-wrap">{entry.text}</span>
                  <Button
                    aria-label={m.forget}
                    disabled={busy === `${scope.key}:${entry.index}`}
                    onClick={() => {
                      const key = `${scope.key}:${entry.index}`
                      if (armed !== key) {
                        setArmed(key)
                        return
                      }
                      setArmed(null)
                      void forget(scope.key, entry.index)
                    }}
                    size="sm"
                    title={m.forgetHint}
                    variant="ghost"
                  >
                    {armed === `${scope.key}:${entry.index}` ? m.confirmForget : m.forget}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}

/**
 * 「凭什么没问你」——边界层的读数（`GET /api/memory/boundary`，只读）。
 *
 * 四块，按「出事时最想知道哪个」排序：
 * ① 熔断（全部暂停，没熔断就不占地方）；
 * ② 每类一行（连采 X/5、窗口内驳回、授没授权）——答「这条为什么还在问我」；
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
