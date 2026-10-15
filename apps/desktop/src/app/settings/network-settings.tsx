import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { Globe } from '@/lib/icons'
import { getNetworkEgress, getNetworkStatus, importNetworkPool, saveNetworkEgress, setNetworkSystemProxy } from '@/plobi'
import type {
  NetworkEgressBlock,
  NetworkEgressRecord,
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
  const [poolNotes, setPoolNotes] = useState<string[]>([])
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [applied, setApplied] = useState('')
  const [saving, setSaving] = useState(false)
  const [proxyBusy, setProxyBusy] = useState(false)
  const [proxyAnswer, setProxyAnswer] = useState<ProxyAnswer | null>(null)

  const load = async () => {
    const fetched = await getNetworkEgress()
    setRecord(fetched)
    setExits(toExitRows(fetched))
    setRules(toRuleRows(fetched))
    setNodes(toNodeRows(fetched))
    setGroups(toGroupRows(fetched))
  }

  useEffect(() => {
    let alive = true
    load().catch((error: unknown) => alive && setError(String(error)))
    getNetworkStatus()
      .then((next) => alive && setStatus(next))
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
    const rule = status?.rules.find((entry) => entry.match === match)
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

  return (
    <SettingsContent>
      <SectionHeading icon={Globe} meta={status?.engine.state ?? ''} title={copy.title} />
      <p className={CAPTION}>{copy.intro}</p>

      <div className="grid gap-1">
        <ListRow
          action={
            <Switch
              aria-label={copy.systemProxy}
              checked={status?.system_proxy?.declared ?? false}
              disabled={!status?.system_proxy?.supported || proxyBusy}
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

      <div className="grid gap-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{copy.exits}</h3>
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

      <div className="flex items-center gap-3">
        <Button disabled={saving} onClick={() => void save()}>
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
          {status.engine.notes.map((note, index) => (
            <p className={CAPTION} key={index}>
              {note}
            </p>
          ))}
          {status.downgrades.map((entry, index) => (
            <p className={CAPTION} key={index}>
              {entry.exit} · {entry.reason}
            </p>
          ))}
        </div>
      ) : null}
    </SettingsContent>
  )
}
