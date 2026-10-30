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
}

const COPY: Record<'en' | 'zh' | 'ja' | 'zh-hant', LedgerCopy> = {
  en: {
    agentLine: agent => `Agent: ${agent}`,
    streakLabel: (streak, threshold) => `${streak}/${threshold} nods in a row`,
    unbound: 'Secretary — no agent bound',
    openPending: 'Review pending',
    empty: 'Nothing remembered yet',
    forget: 'Forget',
    forgetHint: 'Delete this entry and leave a receipt',
    failed: 'Not deleted',
    unavailable: 'Memory is not readable right now'
  },
  zh: {
    agentLine: agent => `这份 Agent：${agent}`,
    streakLabel: (streak, threshold) => `连采 ${streak}/${threshold}`,
    unbound: '总秘书（没有绑定到某个 Agent）',
    openPending: '看等你点头的',
    empty: '还没记住什么',
    forget: '忘掉',
    forgetHint: '删掉这一条，并留一行回执',
    failed: '没删成',
    unavailable: '现在读不到记忆'
  },
  ja: {
    agentLine: agent => `この Agent：${agent}`,
    streakLabel: (streak, threshold) => `連続 ${streak}/${threshold}`,
    unbound: '秘書（エージェント未割当）',
    openPending: '承認待ちを見る',
    empty: 'まだ何も覚えていません',
    forget: '忘れる',
    forgetHint: 'この項目を削除し、記録を残します',
    failed: '削除できませんでした',
    unavailable: '今は記憶を読めません'
  },
  'zh-hant': {
    agentLine: agent => `這份 Agent：${agent}`,
    streakLabel: (streak, threshold) => `連採 ${streak}/${threshold}`,
    unbound: '總秘書（沒有綁定到某個 Agent）',
    openPending: '看等你點頭的',
    empty: '還沒記住什麼',
    forget: '忘掉',
    forgetHint: '刪掉這一條，並留一行回執',
    failed: '沒刪成',
    unavailable: '現在讀不到記憶'
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

  const load = useCallback(async () => {
    try {
      const payload = await window.plobiDesktop.api<LedgersPayload>({ path: '/api/memory/ledgers' })
      setData(payload)
      setError(null)
    } catch {
      setError(m.unavailable)
    }
  }, [m.unavailable])

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
                    onClick={() => void forget(scope.key, entry.index)}
                    size="sm"
                    title={m.forgetHint}
                    variant="ghost"
                  >
                    {m.forget}
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
