/**
 * 一条按会话准入的信源，在设置页抽屉里那份**会话名单**（用户 10-24：
 * 「把微信和钉钉的会话采集都做成做到UI里面去。这样太麻烦了」）。
 *
 * 微信与钉钉共用这一块：数据只来自一条按信源走的路由
 * （`/api/collect/sources/{id}/conversations`），而那条路由背后是这两条信源
 * **各自声明的两只手**（`SourceSpec.conversations` / `admission`）。这一档因此
 * 不认识任何信源的名字——第三方包只要同样声明那两手，它就自动有名单。
 *
 * 为什么不复用日程板那座 `talker-collection`：它讲的是"第一趟要不要开始采"
 * （`review_done`、采集器健康条、完成审查那颗按钮），设置页这份讲的是"每一扇采不采"。
 * 硬合成一座就会把首启那套语义漏进设置页——**同一族数据、两句话，各自的形状留着**。
 *
 * 三种答案分得开是这一档的全部判据：`ok`（拿到了，0 行也是真话）／
 * `unavailable`（有名单这层，但现在拿不到：侧车没起、本机库读不出钥匙）／
 * `not_supported`（这条压根没有"会话"这一层）。把它们画成同一种"空"，
 * 就是下一句"我以为它没东西"。
 */

import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { useI18n } from '@/i18n'

import { type ConversationsPayload, getConversations, setConversationStatus } from './api'

/** `settings.sources` 那一片的话（四语言目录给，这里不造句）。 */
type Labels = ReturnType<typeof useI18n>['t']['settings']['sources']

export function ConversationList({ labels, source }: { labels: Labels, source: string }) {
  const c = labels.conversations

  const [payload, setPayload] = useState<ConversationsPayload | null>(null)
  const [readFailed, setReadFailed] = useState(false)
  // 读失败与**写失败**是两件事：写失败只说那一句"没改成"。把整份名单换成"读不到"会顺手
  // 抹掉其余几十上百行——那比一个错的状态更难查。
  const [writeFailed, setWriteFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [filter, setFilter] = useState('')

  useEffect(() => {
    let alive = true

    setLoading(true)
    getConversations(source)
      .then(next => {
        if (!alive) {
          return
        }

        setPayload(next)
        setReadFailed(false)
      })
      .catch(err => {
        console.error('[settings/sources] conversations read failed', source, err)

        if (alive) {
          setReadFailed(true)
        }
      })
      .finally(() => {
        if (alive) {
          setLoading(false)
        }
      })

    return () => { alive = false }
  }, [source])

  const decide = async (id: string, status: string) => {
    setBusy(id)
    setWriteFailed(false)

    try {
      // 回的是**改完之后的整份名单**：界面照它重画。于是"写没写成"由后端的读数说话，
      // 不由乐观的前端状态说话——写失败时那一行必须还是原来的样子。
      setPayload(await setConversationStatus(source, id, status))
    } catch (err) {
      console.error('[settings/sources] conversation decision failed', source, id, err)
      setWriteFailed(true)
    } finally {
      setBusy('')
    }
  }

  const rows = useMemo(() => {
    const all = payload?.conversations ?? []
    const needle = filter.trim().toLowerCase()

    return needle ? all.filter(row => row.name.toLowerCase().includes(needle)) : all
  }, [filter, payload])

  if (loading) {
    return <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`conversations-${source}-loading`}>{c.loading}</p>
  }

  if (readFailed || !payload) {
    return <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`conversations-${source}-failed`}>{c.unreadable}</p>
  }

  if (payload.state === 'not_supported') {
    return <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`conversations-${source}-none-layer`}>{payload.reason || c.unreadable}</p>
  }

  if (payload.state === 'unavailable') {
    // 那句为什么是后端给的**读数**（侧车为什么没答、钥匙为什么解不出），界面不转述。
    return (
      <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`conversations-${source}-unavailable`}>
        {c.unavailable}
        {' — '}
        {payload.reason}
      </p>
    )
  }

  const excluded = payload.conversations.filter(row => row.status === 'excluded').length

  return (
    <div className="space-y-2" data-testid={`conversations-${source}`}>
      {writeFailed
        ? (
            <p className="text-[length:var(--conversation-caption-font-size)] text-destructive" data-testid={`conversations-${source}-write-failed`}>
              {c.writeFailed}
            </p>
          )
        : null}

      <div className="flex items-center gap-2">
        <span className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`conversations-${source}-summary`}>
          {c.summary.replace('{n}', String(payload.conversations.length)).replace('{excluded}', String(excluded))}
        </span>
        {payload.conversations.length > 8
          ? (
              <Input
                aria-label={c.filter}
                className="h-6 max-w-40 text-[length:var(--conversation-caption-font-size)]"
                onChange={event => setFilter(event.target.value)}
                placeholder={c.filter}
                value={filter}
              />
            )
          : null}
      </div>

      {rows.length === 0
        ? <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{c.empty}</p>
        : null}

      <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
        {rows.map(row => (
          <li className="flex items-center gap-2" data-testid={`conversation-row-${row.id}`} key={row.id}>
            <span className="min-w-0 flex-1 truncate text-[length:var(--conversation-caption-font-size)]" title={row.name}>
              {row.name}
            </span>
            <span className="shrink-0 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
              {row.kind === 'group' ? c.kindGroup : c.kindSingle}
            </span>
            {/* 没定过（＝默认采）**不画徽章**：178 行里挂 178 个灰点，那不是信息，是噪声。 */}
            {row.status !== 'pending'
              ? <span className="shrink-0 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{row.status === 'excluded' ? c.modeExcluded : c.modeKnown}</span>
              : null}
            <Button
              disabled={!!busy}
              onClick={() => { void decide(row.id, row.status === 'excluded' ? 'pending' : 'excluded') }}
              size="xs"
              variant="ghost"
            >
              {row.status === 'excluded' ? c.collect : c.exclude}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}
