import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n'
import { ExternalLink } from '@/lib/icons'
import { getNetworkEgress, getNetworkStatus, saveNetworkEgress } from '@/plobi'
import type { NetworkEgressRecord, NetworkSection, NetworkStatus } from '@/types/plobi'

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

  return {
    ...(source?.network?.force_ipv4 === undefined ? {} : { force_ipv4: source.network.force_ipv4 }),
    egress: {
      ...(egress.engine ? { engine: egress.engine } : {}),
      ...(egress.mirrors && Object.keys(egress.mirrors).length ? { mirrors: egress.mirrors } : {}),
      ...(Object.keys(proxies).length ? { proxies } : {}),
      ...(keptRules.length ? { rules: keptRules } : {})
    }
  }
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
  const [saving, setSaving] = useState(false)

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
    try {
      await saveNetworkEgress(buildSection(exits, rules, record))
      setSaved(true)
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
      <SectionHeading icon={ExternalLink} meta={status?.engine.state ?? ''} title={copy.title} />
      <p className={CAPTION}>{copy.intro}</p>

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
        {saved && !error ? <span className={CAPTION}>{copy.saved}</span> : null}
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
