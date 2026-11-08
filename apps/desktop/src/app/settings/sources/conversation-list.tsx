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

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import type { useI18n } from '@/i18n'

import { type ConversationRow, type ConversationsPayload, getConversations, setConversationStatus } from './api'
import { $rosterFold, decideRoster, isRosterOpen } from './collapse'

/** `settings.sources` 那一片的话（四语言目录给，这里不造句）。 */
type Labels = ReturnType<typeof useI18n>['t']['settings']['sources']

/**
 * 三组的**顺序**（他 10-26 拍的就是这三组：群聊／私聊／公众号）。
 * 判据与词表住在后端（`plobi/collectors/chatlog/client.py` 的 `ROSTER_KINDS` 那段），
 * 这里只定画的次序；认不出的类别（第三方包给的别的词）不丢——排在最后，拿它自己的词上屏，
 * 不并成"私聊"（那是一颗把别人的形状吞掉的合并）。
 */
const KIND_ORDER = ['group', 'private', 'official']

/**
 * `2026-10-26T15:48:03+08:00` → `10-26 15:48`（今年之内不写年）。
 *
 * 这一格用**绝对时间**，而不是行上那套"3 小时前"：167 行里那句相对话要心算，
 * 而名单要问的是"哪一扇还活着"——两个问题不该共用一句人话。
 * 拿不到时刻就回空串（调用方什么都不画），不许拿"现在"顶一个假读数。
 */
function clockLabel(value: string | undefined): string {
  if (!value) {
    return ''
  }

  const at = Date.parse(value)

  if (Number.isNaN(at)) {
    return ''
  }

  const d = new Date(at)
  const pad = (n: number): string => String(n).padStart(2, '0')

  return d.getFullYear() === new Date().getFullYear()
    ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 类别 → 那一组的话（同一个词表也用于行上的旧标签位，一处词表）。 */
function kindLabel(kind: string, labels: Labels): string {
  const table = labels.conversations.kinds as unknown as Record<string, string>

  return table[kind] ?? kind
}

export function ConversationList({ labels, source }: { labels: Labels, source: string }) {
  const c = labels.conversations
  // 折叠状态在**钩子区**取（放在那几个 early return 之后就是违钩子规则，上一刀在大折叠那屏踩过一次）。
  const rosterFold = useStore($rosterFold)

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

  /**
   * 分成他要的那三组。三条判据都写在这里而不是散进 JSX：
   *
   * - **只画真出现了的组**：微信有公众号，钉钉没有 ⇒ 钉钉那屏不该多一段空的"公众号"
   *   （与大折叠那屏"空分类整块不画"同一条判据）；认不出的类别排到最后，不并组。
   * - **组内按"最近一条"倒序**（他 10-26：「加时间，并按它倒序」）。没给时间的排到最后——
   *   空是"这条信源没报这一格"，不是"很旧"，所以拿 `Date.parse` 的 `NaN` 单独一档，不折成 0。
   * - **过滤在分组之前**：他打一个词想看的是"这个名字在哪一组"，不是"某一组里的匹配行"。
   */
  const groups = useMemo(() => {
    const all = payload?.conversations ?? []
    const needle = filter.trim().toLowerCase()
    const kept = needle ? all.filter(row => row.name.toLowerCase().includes(needle)) : all
    const present = [...new Set(kept.map(row => row.kind))]

    const order = [
      ...KIND_ORDER.filter(kind => present.includes(kind)),
      ...present.filter(kind => !KIND_ORDER.includes(kind))
    ]

    const stamp = (row: ConversationRow): number => {
      const at = Date.parse(row.last_at ?? '')

      return Number.isNaN(at) ? -1 : at
    }

    return order.map(kind => {
      const rows = kept.filter(row => row.kind === kind).sort((a, b) => stamp(b) - stamp(a))

      return {
        count: rows.length,
        excluded: rows.filter(row => row.status === 'excluded').length,
        kind,
        rows
      }
    })
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
    <div className="space-y-1" data-testid={`conversations-${source}`}>
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

      {groups.length === 0
        ? <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`conversations-${source}-empty`}>{c.empty}</p>
        : null}

      {/* 三组、每组各自可折（他 10-26：「而且请按类别折叠在抽屉里面」）。
          折起来只藏那一组的行，组头那格永远在——段数与每段几扇是他要的读数。 */}
      {groups.map(group => {
        const open = isRosterOpen(rosterFold, source, group.kind)

        return (
          <section data-testid={`conversations-${source}-group-${group.kind}`} key={group.kind}>
            <button
              aria-expanded={open}
              aria-label={`${kindLabel(group.kind, labels)} — ${open ? labels.groups.collapse : labels.groups.expand}`}
              className="-mx-1 flex w-[calc(100%+0.5rem)] items-center gap-1.5 rounded-md px-1 py-0.5 text-left row-hover"
              data-testid={`conversations-${source}-group-${group.kind}-toggle`}
              onClick={() => { decideRoster(rosterFold, source, group.kind, !open) }}
              type="button"
            >
              <DisclosureCaret className="size-3 shrink-0 text-(--ui-text-tertiary)" open={open} />
              <span className="min-w-0 flex-1 truncate text-[length:var(--conversation-caption-font-size)] font-medium">
                {kindLabel(group.kind, labels)}
              </span>
              {/* 组头只说两件事：几扇、其中几扇被他关了。一扇都没关就不写那半句
                  （与"没定过的不画徽章"同一条判据：画上去是噪声，不是信息）。 */}
              <span className="shrink-0 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
                {group.excluded > 0
                  ? `${group.count} · ${c.excludedOf.replace('{n}', String(group.excluded))}`
                  : String(group.count)}
              </span>
            </button>

            {open
              ? (
                  <ul className="ml-2 space-y-0.5 border-l border-(--ui-stroke-tertiary) py-0.5 pl-2" data-testid={`conversations-${source}-rows-${group.kind}`}>
                    {group.rows.map(row => (
                      <li className="flex items-center gap-2" data-testid={`conversation-row-${row.id}`} key={row.id}>
                        {/* 查不到名字的那一行拿会话号上屏，但**念成号**：灰字 + 一句"没查到名字"。
                            拿一个号假装是名字，比让他看到号更难查——他会以为那是个人。 */}
                        <span
                          className={`min-w-0 flex-1 truncate text-[length:var(--conversation-caption-font-size)] ${row.resolved === false ? 'text-(--ui-text-tertiary)' : ''}`}
                          title={row.resolved === false ? `${row.name} — ${c.unnamed}` : row.name}
                        >
                          {row.name}
                        </span>
                        {row.last_at
                          ? <span className="shrink-0 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid={`conversation-time-${row.id}`}>{clockLabel(row.last_at)}</span>
                          : null}
                        {/* 行上**只有这一颗**（他的原话：「每个条目旁边给一个开关就行了」）。
                            开关不画第三档：开＝采（含"没定过"，默认就是采），关＝不采。 */}
                        <Switch
                          aria-label={`${row.name} — ${row.status === 'excluded' ? c.exclude : c.collect}`}
                          checked={row.status !== 'excluded'}
                          disabled={!!busy}
                          onCheckedChange={next => { void decide(row.id, next ? 'pending' : 'excluded') }}
                          size="xs"
                        />
                      </li>
                    ))}
                  </ul>
                )
              : null}
          </section>
        )
      })}
    </div>
  )
}
