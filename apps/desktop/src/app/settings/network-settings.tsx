import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { Globe } from '@/lib/icons'
import { getNetworkEgress, getNetworkStatus, saveNetworkEgress, setNetworkSystemProxy } from '@/plobi'
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

function toRuleRows(record: NetworkEgressRecord | null): RuleRow[] {
  return (record?.network?.egress?.rules ?? []).map((rule) => ({ match: rule.match, via: rule.via }))
}

function toExitRows(record: NetworkEgressRecord | null): ExitRow[] {
  return Object.entries(record?.network?.egress?.proxies ?? {}).map(([name, entry]) => ({
    name,
    url: entry?.url ?? ''
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
  source: NetworkEgressRecord | null
): NetworkSection {
  const proxies = Object.fromEntries(
    exits.filter((exit) => exit.name.trim() && exit.url.trim()).map((exit) => [exit.name.trim(), { url: exit.url.trim() }])
  )
  const keptRules = rules
    .filter((rule) => rule.match.trim())
    .map((rule) => ({ match: rule.match.trim(), via: rule.via.trim() || 'direct' }))
  const egress = source?.network?.egress ?? {}

  const carried = Object.fromEntries(
    Object.entries(egress).filter(([key]) => key !== 'proxies' && key !== 'rules')
  ) as NetworkEgressBlock

  return {
    ...(source?.network?.force_ipv4 === undefined ? {} : { force_ipv4: source.network.force_ipv4 }),
    egress: {
      ...carried,
      ...(Object.keys(proxies).length ? { proxies } : {}),
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
      const result = await saveNetworkEgress(buildSection(exits, rules, record))
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
                    <option key={name} value={`proxy:${name}`}>
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
