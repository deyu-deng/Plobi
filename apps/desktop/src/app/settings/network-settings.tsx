import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { Globe } from '@/lib/icons'
import { getNetworkEgress, getNetworkImportClients, getNetworkStatus, importNetworkPool, importNetworkSubscription, saveNetworkEgress, setNetworkSystemProxy } from '@/plobi'
import type {
  NetworkEgressBlock,
  NetworkEgressRecord,
  NetworkImportClients,
  NetworkSection,
  NetworkStatus,
  NetworkStatusSystemProxy
} from '@/types/plobi'

import { ListRow, SectionHeading, SettingsContent } from './primitives'

const CAPTION = 'text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)'

interface RuleRow {
  match: string
  via: string
}

interface ExitRow {
  name: string
  url: string
}

/**
 * One engine-side member. A credential is **never a value in this row** — the field holds the
 * name of the environment variable that holds it (`password_env` / `uuid_env`), so no cleartext
 * ever round-trips through a settings pane, an HTTP body or a screenshot (纸 §23: 凭据只以 `*_env`
 * 名字进表，值在编译器发射时读环境).
 */
interface NodeRow {
  cred: string
  credKey: string
  name: string
  port: string
  raw: Record<string, unknown>
  server: string
  type: string
}

interface GroupRow {
  include: string
  name: string
  raw: Record<string, unknown>
  type: string
}

const CRED_FIELD: Record<string, string> = {
  http: 'password_env',
  https: 'password_env',
  hysteria2: 'password_env',
  shadowsocks: 'password_env',
  socks5: 'password_env',
  socks5h: 'password_env',
  ss: 'password_env',
  trojan: 'password_env',
  tuic: 'password_env',
  vless: 'uuid_env',
  vmess: 'uuid_env'
}

function toRuleRows(record: NetworkEgressRecord | null): RuleRow[] {
  return (record?.network?.egress?.rules ?? []).map((rule) => ({ match: rule.match, via: rule.via }))
}

function toExitRows(record: NetworkEgressRecord | null): ExitRow[] {
  return Object.entries(record?.network?.egress?.proxies ?? {}).map(([name, entry]) => ({
    name,
    url: entry?.url ?? ''
  }))
}

/** 合并而不是覆盖：同名以进来那份为准（订阅更新就是更新），不同名的手写条目保留。 */
export function mergeByName<T extends { name: string }>(current: T[], incoming: T[]): T[] {
  const byName = new Map<string, T>()

  for (const row of current) {
    byName.set(row.name.trim(), row)
  }
  for (const row of incoming) {
    byName.set(row.name.trim(), row)
  }
  return [...byName.values()].filter((row) => row.name.trim())
}

export function toNodeRows(record: NetworkEgressRecord | null): NodeRow[] {
  return Object.entries(record?.network?.egress?.nodes ?? {}).map(([name, entry]) => {
    const raw = { ...((entry ?? {}) as Record<string, unknown>) }
    const type = String(raw.type ?? 'ss')
    const credKey = CRED_FIELD[type.toLowerCase()] ?? ''

    return {
      cred: credKey ? String(raw[credKey] ?? '') : '',
      credKey,
      name,
      port: raw.port === undefined ? '' : String(raw.port),
      raw,
      server: String(raw.server ?? ''),
      type
    }
  })
}

export function toGroupRows(record: NetworkEgressRecord | null): GroupRow[] {
  return Object.entries(record?.network?.egress?.groups ?? {}).map(([name, entry]) => ({
    include: Array.isArray(entry?.include) ? entry.include.join(', ') : String(entry?.include ?? ''),
    name,
    raw: { ...(entry ?? {}) },
    type: entry?.type ?? 'url-test'
  }))
}

/** Rebuild the `network:` section from the two editable lists.
 *
 *  Empty pieces are dropped rather than written as `{}` / `[]`: an empty rules
 *  list means "no opinion" to the parser, and leaving a stray empty map on disk
 *  reads later like a rule that was forgotten about.
 *
 *  Everything the pane does not edit is carried through verbatim, because the save
 *  is a whole-section replace: dropping `engine`, the node pool or `system_proxy`
 *  here would silently undo them every time someone presses Save.
 */
export function buildSection(
  exits: ExitRow[],
  rules: RuleRow[],
  nodes: NodeRow[],
  groups: GroupRow[],
  source: NetworkEgressRecord | null
): NetworkSection {
  const proxies = Object.fromEntries(
    exits.filter((exit) => exit.name.trim() && exit.url.trim()).map((exit) => [exit.name.trim(), { url: exit.url.trim() }])
  )
  const keptRules = rules
    .filter((rule) => rule.match.trim())
    .map((rule) => ({ match: rule.match.trim(), via: rule.via.trim() || 'direct' }))
  const egress = source?.network?.egress ?? {}

  const nodesBlock = Object.fromEntries(
    nodes
      .filter((node) => node.name.trim())
      .map((node) => {
        const entry: Record<string, unknown> = { ...node.raw, type: node.type }

        if (node.server.trim()) {
          entry.server = node.server.trim()
        }
        if (node.port.trim()) {
          entry.port = Number(node.port.trim())
        }
        if (node.credKey) {
          // The pane renders exactly one credential control, and it holds a NAME. Whatever
          // cleartext sibling happens to sit in the carried entry goes out with this save —
          // `password_env` and `password` must not coexist with the value winning by accident.
          for (const secret of ['password', 'uuid', 'username', 'tls-alpn', 'client_id']) {
            delete entry[secret]
          }
          if (node.cred.trim()) {
            entry[node.credKey] = node.cred.trim()
          }
        }

        return [node.name.trim(), entry]
      })
  )
  const groupsBlock = Object.fromEntries(
    groups
      .filter((group) => group.name.trim())
      .map((group) => {
        const listed = group.include.split(',').map((name) => name.trim()).filter(Boolean)
        const entry: Record<string, unknown> = {
          ...group.raw,
          include: group.include.trim() === '*' ? '*' : listed,
          type: group.type
        }

        return [group.name.trim(), entry]
      })
  )

  const carried = Object.fromEntries(
    Object.entries(egress).filter(
      ([key]) => key !== 'proxies' && key !== 'rules' && key !== 'nodes' && key !== 'groups'
    )
  ) as NetworkEgressBlock

  return {
    ...(source?.network?.force_ipv4 === undefined ? {} : { force_ipv4: source.network.force_ipv4 }),
    egress: {
      ...carried,
      ...(Object.keys(proxies).length ? { proxies } : {}),
      ...(Object.keys(nodesBlock).length ? { nodes: nodesBlock } : {}),
      ...(Object.keys(groupsBlock).length ? { groups: groupsBlock } : {}),
      ...(keptRules.length ? { rules: keptRules } : {})
    }
  }
}

export interface EngineAnswer {
  state: string
  detail: string
  probe?: string
}

/** The takeover answer, in the backend's words: state, reason, and the lines that
 *  spell out what it wrote (PAC path, the value `off` will hand back). */
export interface ProxyAnswer extends EngineAnswer {
  lines?: string[]
}

/**
 * One line saying what supervision did with the table just saved — the answer to
 * "配了不生效". Words come from the backend verbatim; this pane never re-derives
 * whether the engine is up (裁定 94: one reading, several renderings).
 */
export function appliedNote(engineLabel: string, engine?: EngineAnswer): string {
  if (!engine) {
    return ''
  }

  return `${engineLabel}: ${engine.state} — ${engine.detail}${engine.probe ? ` (${engine.probe})` : ''}`
}

/**
 * What the machine-wide half is actually doing, in four distinct sentences.
 *
 * `declared` is the intent (what the table says) and `ours` is the registry — the
 * two disagree whenever a takeover was refused or someone else rewrote the
 * settings underneath us, so the pane must not collapse them into one word. No
 * branch is optimistic: a missing reading says nothing rather than "off".
 */
export function proxyStateText(
  copy: {
    proxyDeclaredNotApplied: string
    proxyNotOurs: string
    proxyOurs: string
    proxyUnsupported: string
  },
  proxy?: NetworkStatusSystemProxy
): string {
  if (!proxy) {
    return ''
  }

  if (!proxy.supported) {
    return copy.proxyUnsupported
  }

  if (proxy.ours) {
    return copy.proxyOurs
  }

  return proxy.declared ? copy.proxyDeclaredNotApplied : copy.proxyNotOurs
}

export function NetworkSettings() {
  const { t } = useI18n()
  const copy = t.settings.network
  const [record, setRecord] = useState<NetworkEgressRecord | null>(null)
  const [status, setStatus] = useState<NetworkStatus | null>(null)
  const [exits, setExits] = useState<ExitRow[]>([])
  const [rules, setRules] = useState<RuleRow[]>([])
  const [nodes, setNodes] = useState<NodeRow[]>([])
  const [groups, setGroups] = useState<GroupRow[]>([])
  const [poolPath, setPoolPath] = useState('')
  const [subUrl, setSubUrl] = useState('')
  //: 用户 10-30 问到的第二种配置方式：把别人给的整份配置**粘进来**。解析器一直支持
  //: （yaml / base64 / 一行一个链接），只是没有入口。
  const [pasted, setPasted] = useState('')
  const [localClients, setLocalClients] = useState<NonNullable<NetworkImportClients['clients']>>([])
  const [poolNotes, setPoolNotes] = useState<string[]>([])
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [applied, setApplied] = useState('')
  const [saving, setSaving] = useState(false)
  const [proxyBusy, setProxyBusy] = useState(false)
  const [proxyAnswer, setProxyAnswer] = useState<ProxyAnswer | null>(null)
  //: Whether the table actually came back. Saving from a draft that never loaded
  //: writes the empty draft over the real table — measured 10-29: the whole table
  //: (46 members, 17 groups, 3 rules) went to `{system_proxy: {enabled: false}}`
  //: because the pane loaded nothing and then saved. Nothing may be written until
  //: a load has succeeded.
  const [loaded, setLoaded] = useState(false)

  const load = async () => {
    const fetched = await getNetworkEgress()
    setRecord(fetched)
    setExits(toExitRows(fetched))
    setRules(toRuleRows(fetched))
    setNodes(toNodeRows(fetched))
    setGroups(toGroupRows(fetched))
    setLoaded(true)
  }

  useEffect(() => {
    let alive = true
    load().catch((error: unknown) => alive && setError(String(error)))
    getNetworkStatus()
      .then((next) => alive && setStatus(next))
      .catch(() => {})
    getNetworkImportClients()
      .then((next) => alive && setLocalClients(next.clients ?? []))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const save = async () => {
    setSaving(true)
    setError('')
    setApplied('')
    try {
      const result = await saveNetworkEgress(buildSection(exits, rules, nodes, groups, record))
      setSaved(true)
      setApplied(appliedNote(copy.engine, result.engine))
      // The backend re-reads the table on save; refresh both so the badges and
      // the live status describe what is now on disk, not what we typed.
      await load()
      setStatus(await getNetworkStatus())
    } catch (cause: unknown) {
      // A refused save is the parser talking, not a transport failure — its
      // words are the useful part, so they go on the pane instead of a toast.
      setError(cause instanceof Error ? cause.message : String(cause))
      setSaved(false)
    } finally {
      setSaving(false)
    }
  }

  const setProxy = async (enabled: boolean) => {
    setProxyBusy(true)
    setError('')

    try {
      const result = await setNetworkSystemProxy(enabled)

      setProxyAnswer(result.proxy ?? null)
      // The switch is a key inside the table, and the editor holds a copy of that
      // table — reload so the next Save cannot write back the pre-toggle block.
      await load()
      setStatus(await getNetworkStatus())
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setProxyBusy(false)
    }
  }

  const badgeFor = (match: string) => {
    // 后端少一个键就把整屏炸掉过一次（测试里就是这么喂的）——每一处都当「可能没有」读。
    const rule = (status?.rules ?? []).find((entry) => entry.match === match)
    if (!rule) return ''
    if (rule.engine_only) return copy.engineOnly
    if (rule.env_dropped) return copy.envDropped
    return ''
  }

  const exitNames = exits.map((exit) => exit.name.trim()).filter(Boolean)
  const engineNames = [...nodes.map((node) => node.name.trim()), ...groups.map((group) => group.name.trim())].filter(
    Boolean
  )

  const importPool = async () => {
    setImporting(true)
    setError('')
    setPoolNotes([])
    try {
      const answer = await importNetworkPool(poolPath.trim())

      // The import lands in the rows, not on disk: the user still has to press Save, and a
      // refusal from the parser is still the parser talking (a pool that names a group member
      // that is not under `nodes` must not become a half-written table).
      setNodes(toNodeRows({ network: { egress: answer.egress } } as unknown as NetworkEgressRecord))
      setGroups(toGroupRows({ network: { egress: answer.egress } } as unknown as NetworkEgressRecord))
      const envNames = answer.env_names ?? []

      setPoolNotes([...(answer.notes ?? []), ...(envNames.length ? [`需要这些环境变量：${envNames.join(', ')}`] : [])])
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setImporting(false)
    }
  }

  /**
   * 订阅：点一下才拉（不自动拉，不静默重试）。回来的成员与组**并进**当前行——同一个名字以订阅为准，
   * 手写的其他成员保留，因为"更新我的订阅"不该把用户自己加的节点抹掉。仍然要点保存才写盘。
   */
  const importSubscription = async () => {
    setImporting(true)
    setError('')
    try {
      // The link is a credential: storing it in the root .env now means 「更新」 later needs
      // neither the app nor retyping (CLI: `plobi network import-subscription --refresh`).
      const answer = await importNetworkSubscription({ keepGroups: true, save: true, url: subUrl.trim() })
      applyImport(answer)
      if (!answer.ok && !(answer.notes ?? []).length) {
        setError('订阅里没有可用的成员')
      }
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setImporting(false)
    }
  }

  /** 粘贴一段配置就导入：与链接、文件、客户端缓存走同一个解析器（一份 yaml / base64 / 链接清单）。 */
  const importPasted = async () => {
    setImporting(true)
    setError('')
    try {
      const answer = await importNetworkSubscription({ keepGroups: true, text: pasted.trim() })
      applyImport(answer)
      if (answer.ok) setPasted('')
      if (!answer.ok && !(answer.notes ?? []).length) setError('这段配置里没有可用的成员')
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setImporting(false)
    }
  }

  /**
   * 从本机客户端自己的缓存里读订阅。有些订阅商只给软件、不给"复制订阅链接"的按钮——但它拉下来的
   * 配置就在这台机器上（DPAPI 加密，用户的用户上下文），读它比让人换订阅商实际得多。
   */
  const importFromClient = async (client: string) => {
    setImporting(true)
    setError('')
    try {
      const answer = await importNetworkSubscription({ client })
      applyImport(answer)
      if (!answer.ok && !(answer.notes ?? []).length) {
        setError('这个客户端的缓存里没有可用的成员')
      }
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setImporting(false)
    }
  }

  /** 一次导入的共同落法：成员与组并进当前行，凭据名字与格式写进 notes。 */
  const applyImport = (answer: {
    format?: string
    groups?: NetworkEgressBlock['groups']
    nodes?: NetworkEgressBlock['nodes']
    notes?: string[]
    ok?: boolean
    secret_env_names?: string[]
  }) => {
    const fetched = { network: { egress: { groups: answer.groups ?? {}, nodes: answer.nodes ?? {} } } } as unknown as NetworkEgressRecord
    const nextNodes = toNodeRows(fetched)
    const nextGroups = toGroupRows(fetched)

    setNodes((current) => mergeByName(current, nextNodes))
    setGroups((current) => mergeByName(current, nextGroups))
    const envNames = answer.secret_env_names ?? []

    setPoolNotes([
      ...(answer.notes ?? []),
      ...(answer.format ? [`认出的格式：${answer.format}；成员 ${nextNodes.length} 个、组 ${nextGroups.length} 个`] : []),
      ...(envNames.length ? [`凭据已写进根 .env，表里只留这些名字：${envNames.join(', ')}`] : [])
    ])
  }

  // 简约控制台（用户 10-30：像 ikuuu 那样"一张卡 + 一个宫格"）：
  // 「默认出口」就是兜底规则指的那个组/节点；「按规则 / 全部走代理」只增删 direct 那几条，
  // 兜底规则本身不动——它一直是"未匹配的都去哪"，改它等于改整机行为。
  const catchAll = (() => {
    const via = rules.length ? rules[rules.length - 1].via : ''
    return via.startsWith('proxy:') ? via.slice('proxy:'.length) : ''
  })()
  const ruleMode: 'rules' | 'global' = rules.some(
    (row) => row.match.trim() === 'geosite:cn' && row.via === 'direct'
  )
    ? 'rules'
    : 'global'

  const setCatchAll = (target: string) => {
    const via = `proxy:${target}`
    if (!rules.length) {
      setRules([{ match: '*', via }])
      return
    }
    const next = [...rules]
    next[next.length - 1] = { ...next[next.length - 1], via }
    setRules(next)
  }

  const setRuleMode = (mode: 'rules' | 'global') => {
    const without = rules.filter((row) => row.match.trim() !== 'geosite:cn')
    if (mode === 'global' || !without.length) {
      setRules(without)
      return
    }
    const last = without[without.length - 1]
    setRules([...without.slice(0, -1), { match: 'geosite:cn', via: 'direct' }, last])
  }

  return (
    <SettingsContent>
      <div title={copy.intro}>
        <SectionHeading icon={Globe} meta={status?.engine.state ?? ''} title={copy.title} />
      </div>
      {!loaded ? (
        <p className="text-sm text-(--ui-text-danger)">读不到当前的出口表：先别保存（保存会把表清空）</p>
      ) : null}
      <div className={CAPTION}>
        {[
          `${copy.engine}: ${status?.engine.state ?? '—'}`,
          `${copy.nodes}: ${nodes.length}`,
          `${copy.groups}: ${groups.length}`,
          `${copy.rules}: ${rules.length}`,
          `${copy.proxyApplied}: ${status?.system_proxy?.ours ? '✓' : '×'}`
        ].join('   ·   ')}
      </div>

      <div className="grid gap-1">
        <ListRow
          action={
            <Switch
              aria-label={copy.systemProxy}
              checked={status?.system_proxy?.declared ?? false}
              disabled={!loaded || !status?.system_proxy?.supported || proxyBusy}
              onCheckedChange={(on) => void setProxy(on)}
            />
          }
          description={proxyStateText(copy, status?.system_proxy)}
          title={copy.systemProxy}
        />
        {proxyAnswer ? <p className={CAPTION}>{appliedNote(copy.proxyApplied, proxyAnswer)}</p> : null}
        {(proxyAnswer?.lines ?? []).map((line, index) => (
          <p className={CAPTION} key={index}>
            {line}
          </p>
        ))}
      </div>

      {/* 一张卡：现在走哪个出口、引擎在不在、按规则还是全部走代理 */}
      <div className="grid gap-3 rounded-xl border border-(--stroke-nous) bg-(--ui-chat-bubble-background) p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="grid gap-0.5">
            <span className={CAPTION}>当前默认出口</span>
            <span className="text-lg font-semibold">{catchAll || '直连'}</span>
            <span className={CAPTION}>
              {copy.engine}: {status?.engine.state ?? '—'}
              {status?.engine.probe ? ` · ${status.engine.probe}` : ''}
            </span>
          </div>
          <div className="flex shrink-0 overflow-hidden rounded-lg border border-(--stroke-nous) text-sm">
            {(['rules', 'global'] as const).map((mode) => (
              <button
                className={`px-3 py-1.5 ${
                  ruleMode === mode ? 'bg-(--theme-primary) text-white' : 'text-(--ui-text-secondary)'
                }`}
                key={mode}
                onClick={() => setRuleMode(mode)}
                type="button"
              >
                {mode === 'rules' ? '按规则' : '全部走代理'}
              </button>
            ))}
          </div>
        </div>

        {groups.length ? (
          <div className="flex flex-wrap gap-1.5">
            {groups.map((group) => (
              <button
                className={`rounded-full border border-(--stroke-nous) px-2.5 py-1 text-xs ${
                  catchAll === group.name ? 'bg-(--theme-primary) text-white' : 'text-(--ui-text-secondary)'
                }`}
                key={group.name}
                onClick={() => setCatchAll(group.name)}
                type="button"
              >
                {group.name}
              </button>
            ))}
          </div>
        ) : null}

        {nodes.length ? (
          <div className="grid max-h-56 grid-cols-2 gap-1.5 overflow-auto sm:grid-cols-3 lg:grid-cols-4">
            {nodes.map((node) => (
              <button
                className={`truncate rounded-lg border border-(--stroke-nous) px-2 py-1.5 text-left text-xs ${
                  catchAll === node.name ? 'bg-(--theme-primary) text-white' : ''
                }`}
                key={node.name}
                onClick={() => setCatchAll(node.name)}
                title={node.server}
                type="button"
              >
                {node.name}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {/* 其余是"改表"的活，收起来；要动再展开 */}
      <details className="grid gap-2">
        <summary className="cursor-pointer text-sm text-(--ui-text-secondary)">
          高级：出口 / 规则 / 成员表 / 组 / 导入 / 引擎
        </summary>

      <div className="grid gap-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{copy.exits}</h3>
          {exits.length === 0 ? <p className={CAPTION}>还没有出口：加一条，或从订阅导入</p> : null}
          <Button onClick={() => setExits([...exits, { name: '', url: '' }])} size="sm" variant="outline">
            {copy.addExit}
          </Button>
        </div>
        {exits.map((exit, index) => (
          <div className="flex items-center gap-2" key={index}>
            <Input
              className="w-40"
              onChange={(event) =>
                setExits(exits.map((row, i) => (i === index ? { ...row, name: event.target.value } : row)))
              }
              placeholder={copy.name}
              value={exit.name}
            />
            <Input
              onChange={(event) =>
                setExits(exits.map((row, i) => (i === index ? { ...row, url: event.target.value } : row)))
              }
              placeholder="http://127.0.0.1:7890"
              value={exit.url}
            />
            <Button onClick={() => setExits(exits.filter((_, i) => i !== index))} size="icon" variant="ghost">
              ×
            </Button>
          </div>
        ))}
      </div>

      <div className="grid gap-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{copy.rules}</h3>
          {rules.length === 0 ? <p className={CAPTION}>还没有规则：默认全部直连</p> : null}
          <Button onClick={() => setRules([...rules, { match: '', via: 'direct' }])} size="sm" variant="outline">
            {copy.addRule}
          </Button>
        </div>
        {rules.map((rule, index) => {
          const badge = badgeFor(rule.match)
          const isLast = index === rules.length - 1
          return (
            <div className="grid gap-1" key={index}>
              <div className="flex items-center gap-2">
                <Input
                  onChange={(event) =>
                    setRules(rules.map((row, i) => (i === index ? { ...row, match: event.target.value } : row)))
                  }
                  placeholder={copy.match}
                  value={rule.match}
                />
                <select
                  className="rounded-md border px-2 py-1 text-sm"
                  onChange={(event) =>
                    setRules(rules.map((row, i) => (i === index ? { ...row, via: event.target.value } : row)))
                  }
                  value={rule.via}
                >
                  <option value="direct">{copy.direct}</option>
                  {exitNames.map((name) => (
                    <option key={`p-${name}`} value={`proxy:${name}`}>
                      {name}
                    </option>
                  ))}
                  {engineNames
                    .filter((name) => !exitNames.includes(name))
                    .map((name) => (
                      <option key={`n-${name}`} value={name}>
                        {name}
                      </option>
                    ))}
                </select>
                <Button onClick={() => setRules(rules.filter((_, i) => i !== index))} size="icon" variant="ghost">
                  ×
                </Button>
              </div>
              {(badge || isLast) && (
                <p className={CAPTION}>
                  {isLast && rule.match.trim() !== '*' ? `${copy.catchAll}` : ''}
                  {badge ? ` · ${badge}` : ''}
                </p>
              )}
            </div>
          )
        })}
      </div>

      <div className="grid gap-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{copy.nodes}</h3>
          {nodes.length === 0 ? <p className={CAPTION}>还没有成员：用下面的订阅导入</p> : null}
          <Button
            disabled={importing || !poolPath.trim()}
            onClick={() => void importPool()}
            size="sm"
            variant="outline"
          >
            {importing ? '…' : copy.importPool}
          </Button>
        </div>
        <Input onChange={(event) => setPoolPath(event.target.value)} placeholder={copy.importPath} value={poolPath} />
        <div className="flex items-center gap-2">
          <Input
            className="flex-1"
            onChange={(event) => setSubUrl(event.target.value)}
            placeholder={copy.subUrl}
            value={subUrl}
          />
          <Button disabled={importing || !subUrl.trim()} onClick={() => void importSubscription()} size="sm" variant="outline">
            {importing ? '…' : copy.importSub}
          </Button>
        </div>
        <div className="grid gap-1">
          <textarea
            className="min-h-20 w-full rounded-md border border-(--stroke-nous) bg-transparent p-2 font-mono text-xs text-(--ui-text-secondary)"
            onChange={(event) => setPasted(event.target.value)}
            placeholder="或者把整份配置粘在这里：Clash/mihomo 的 yaml、一行一个 ss:// / vmess:// 链接、或 base64"
            value={pasted}
          />
          <div>
            <Button
              disabled={importing || !pasted.trim()}
              onClick={() => void importPasted()}
              size="sm"
              variant="outline"
            >
              {importing ? '…' : '导入粘贴的配置'}
            </Button>
          </div>
        </div>
        {localClients.map((client) => (
          <div className="flex items-center gap-2" key={client.id}>
            <p className={CAPTION}>
              {copy.importClient}: {client.note}
              {client.present ? '' : ` — ${copy.importClientMissing}`}
            </p>
            <Button
              disabled={importing || !client.present}
              onClick={() => void importFromClient(client.id)}
              size="sm"
              variant="outline"
            >
              {importing ? '…' : copy.importClientButton}
            </Button>
          </div>
        ))}
        {poolNotes.map((note, index) => (
          <p className={CAPTION} key={index}>
            {note}
          </p>
        ))}
        {nodes.map((node, index) => (
          <div className="grid gap-1" key={index}>
            <div className="flex items-center gap-2">
              <Input
                className="w-36"
                onChange={(event) => setNodes(nodes.map((row, i) => (i === index ? { ...row, name: event.target.value } : row)))}
                placeholder={copy.name}
                value={node.name}
              />
              <Input
                className="w-28"
                onChange={(event) => setNodes(nodes.map((row, i) => (i === index ? { ...row, type: event.target.value } : row)))}
                placeholder={copy.type}
                value={node.type}
              />
              <Input
                onChange={(event) => setNodes(nodes.map((row, i) => (i === index ? { ...row, server: event.target.value } : row)))}
                placeholder={copy.server}
                value={node.server}
              />
              <Input
                className="w-24"
                onChange={(event) => setNodes(nodes.map((row, i) => (i === index ? { ...row, port: event.target.value } : row)))}
                placeholder={copy.port}
                value={node.port}
              />
              <Button onClick={() => setNodes(nodes.filter((_, i) => i !== index))} size="icon" variant="ghost">
                ×
              </Button>
            </div>
            {node.credKey ? (
              <Input
                onChange={(event) => setNodes(nodes.map((row, i) => (i === index ? { ...row, cred: event.target.value } : row)))}
                placeholder={node.credKey}
                value={node.cred}
              />
            ) : null}
            {node.credKey ? <p className={CAPTION}>{copy.credEnv}</p> : null}
          </div>
        ))}
        <Button onClick={() => setNodes([...nodes, { cred: '', credKey: 'password_env', name: '', port: '', raw: {}, server: '', type: 'ss' }])} size="sm" variant="outline">
          {copy.addNode}
        </Button>
      </div>

      <div className="grid gap-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{copy.groups}</h3>
          {groups.length === 0 ? <p className={CAPTION}>还没有组：规则可以指的地方</p> : null}
          <Button
            onClick={() => setGroups([...groups, { include: '', name: '', raw: {}, type: 'url-test' }])}
            size="sm"
            variant="outline"
          >
            {copy.addGroup}
          </Button>
        </div>
        {groups.map((group, index) => (
          <div className="grid gap-1" key={index}>
            <div className="flex items-center gap-2">
              <Input
                className="w-36"
                onChange={(event) => setGroups(groups.map((row, i) => (i === index ? { ...row, name: event.target.value } : row)))}
                placeholder={copy.name}
                value={group.name}
              />
              <Input
                className="w-28"
                onChange={(event) => setGroups(groups.map((row, i) => (i === index ? { ...row, type: event.target.value } : row)))}
                placeholder={copy.type}
                value={group.type}
              />
              <Button onClick={() => setGroups(groups.filter((_, i) => i !== index))} size="icon" variant="ghost">
                ×
              </Button>
            </div>
            <Input
              onChange={(event) => setGroups(groups.map((row, i) => (i === index ? { ...row, include: event.target.value } : row)))}
              placeholder={copy.members}
              value={group.include}
            />
          </div>
        ))}
      </div>

      </details>

      <div className="flex items-center gap-3">
        <Button disabled={saving || !loaded} onClick={() => void save()}>
          {saving ? '…' : copy.save}
        </Button>
        {saved && !error ? <span className={CAPTION}>{copy.saved}{applied ? ` · ${applied}` : ''}</span> : null}
        {error ? (
          <span className="text-sm text-(--ui-text-danger)">
            {copy.refused}: {error}
          </span>
        ) : null}
      </div>

      {status ? (
        <div className="grid gap-1">
          <h3 className="text-sm font-medium">{copy.status}</h3>
          <ListRow
            description={`${copy.engine}: ${status.engine.state} — ${status.engine.detail}${
              status.engine.probe ? ` (${status.engine.probe})` : ''
            }`}
            title={`127.0.0.1:${status.engine.mixed_port || 0}`}
          />
          {(status.engine.notes ?? []).map((note, index) => (
            <p className={CAPTION} key={index}>
              {note}
            </p>
          ))}
          {(status.downgrades ?? []).map((entry, index) => (
            <p className={CAPTION} key={index}>
              {entry.exit} · {entry.reason}
            </p>
          ))}
        </div>
      ) : null}
    </SettingsContent>
  )
}
