import { useEffect, useState } from 'react'

import { useI18n } from '@/i18n'
import { type IconComponent } from '@/lib/icons'
import { deleteEnvVar, getEnvVars, probeEnvVar, revealEnvVar, setEnvVar } from '@/plobi'
import { notify, notifyError } from '@/store/notifications'
import type { EnvProbeResult, EnvVarInfo } from '@/types/plobi'

import { asText, includesQuery, redactedValue, withoutKey } from './helpers'
import { Pill } from './primitives'
import type { EnvRowProps } from './types'

// Shared filter used by every credential surface (Providers + Keys pages):
// category gate first, then a free-text match across key name + description.
export function filterEnv(info: EnvVarInfo, key: string, q: string, cat: string, extra?: string): boolean {
  if (asText(info.category) !== cat) {
    return false
  }

  if (!q) {
    return true
  }

  return (
    key.toLowerCase().includes(q) ||
    includesQuery(info.description, q) ||
    Boolean(extra && extra.toLowerCase().includes(q))
  )
}

export function SettingsCategoryHeading({ count, icon: Icon, title }: CategoryHeadingProps) {
  return (
    <div className="mb-3 flex items-center gap-2 text-[length:var(--conversation-text-font-size)] font-medium">
      <Icon className="size-4 text-muted-foreground" />
      <span>{title}</span>
      {count && <Pill>{count}</Pill>}
    </div>
  )
}

// Owns the env-var fetch + the edit/reveal/save/delete lifecycle so multiple
// credential pages (Providers, Keys) share one source of truth and one set of
// mutation handlers instead of duplicating the plumbing.
export function useEnvCredentials(options: { enabled?: boolean } = {}): UseEnvCredentials {
  const { t } = useI18n()
  const credentials = t.settings.credentials
  const toolsets = t.settings.toolsets
  const enabled = options.enabled !== false
  const [vars, setVars] = useState<Record<string, EnvVarInfo> | null>(null)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [revealed, setRevealed] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  // Which row is mid-probe, and the verdicts already returned. Kept next to
  // saving/edits because a verdict is only meaningful for a specific value.
  const [testing, setTesting] = useState<string | null>(null)
  const [probeResults, setProbeResults] = useState<Record<string, EnvProbeResult>>({})

  // Best-effort cleanup of a retired localStorage flag (global "Show
  // advanced" toggle) — everything in these views is configuration-level.
  useEffect(() => {
    try {
      window.localStorage.removeItem('desktop.settings.keys.show_advanced')
    } catch {
      // Ignore — old key cleanup is best-effort.
    }
  }, [])

  useEffect(() => {
    // A view that does not show a single key must not ask for them: the read
    // costs the backend a credential probe per provider (30 s and a timeout on
    // this machine), and its failure is a toast the user cannot act on
    // (2026-11-10: the Subscriptions screen raised 「API keys failed to load」
    // although it shows no key at all).
    if (!enabled) {
      return
    }

    let cancelled = false

    void (async () => {
      try {
        const next = await getEnvVars()

        if (!cancelled) {
          setVars(next)
        }
      } catch (err) {
        notifyError(err, t.settings.keys.failedLoad)
      }
    })()

    return () => void (cancelled = true)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount; copy is stable
  }, [enabled])

  function patchVar(key: string, patch: Partial<Pick<EnvVarInfo, 'is_set' | 'redacted_value'>>) {
    setVars(c => (c ? { ...c, [key]: { ...c[key], ...patch } } : c))
  }

  function clearLocalState(key: string) {
    setEdits(c => withoutKey(c, key))
    setRevealed(c => withoutKey(c, key))
    // A verdict belongs to the value that produced it. Once that value is saved
    // or removed, an old "Connected" badge would be vouching for a credential
    // nobody tested — drop it and let the user press Test again.
    setProbeResults(c => withoutKey(c, key))
  }

  // Live check for a credential that is not a model provider (translation
  // engines). A typed-but-unsaved draft is what gets tested when there is one,
  // so a wrong key is caught before it ever lands in .env.
  async function handleProbe(key: string) {
    const draft = (edits[key] ?? '').trim()
    setTesting(key)

    try {
      const result = await probeEnvVar(key, draft || undefined)
      // supported:false means the backend has no probe for this key; saying
      // nothing beats inventing a verdict.
      setProbeResults(c => (result.supported === false ? withoutKey(c, key) : { ...c, [key]: result }))
    } catch (err) {
      setProbeResults(c => ({
        ...c,
        [key]: {
          message: err instanceof Error ? err.message : '',
          ok: false,
          reachable: false
        }
      }))
    } finally {
      setTesting(null)
    }
  }

  async function handleSave(key: string) {
    const value = edits[key]

    if (!value) {
      return
    }

    setSaving(key)

    try {
      await setEnvVar(key, value)
      patchVar(key, { is_set: true, redacted_value: redactedValue(value) })
      clearLocalState(key)
      notify({ kind: 'success', title: toolsets.savedTitle, message: toolsets.savedMessage(key) })
    } catch (err) {
      notifyError(err, toolsets.failedSave(key))
    } finally {
      setSaving(null)
    }
  }

  // Direct save for a known value (no edit-state round-trip) — used by the
  // onboarding-style key form, which owns its own input. Returns a result so
  // the form can surface inline errors instead of only toasting.
  async function saveValue(key: string, value: string): Promise<{ message?: string; ok: boolean }> {
    const trimmed = value.trim()

    if (!trimmed) {
      return { message: credentials.enterValueFirst, ok: false }
    }

    setSaving(key)

    try {
      await setEnvVar(key, trimmed)
      patchVar(key, { is_set: true, redacted_value: redactedValue(trimmed) })
      clearLocalState(key)
      notify({ kind: 'success', message: toolsets.savedMessage(key), title: toolsets.savedTitle })

      return { ok: true }
    } catch (err) {
      notifyError(err, toolsets.failedSave(key))

      return { message: err instanceof Error ? err.message : credentials.couldNotSave, ok: false }
    } finally {
      setSaving(null)
    }
  }

  async function handleClear(key: string) {
    if (!window.confirm(toolsets.removeConfirm(key))) {
      return
    }

    setSaving(key)

    try {
      await deleteEnvVar(key)
      patchVar(key, { is_set: false, redacted_value: null })
      clearLocalState(key)
      notify({ kind: 'success', title: toolsets.removedTitle, message: toolsets.removedMessage(key) })
    } catch (err) {
      notifyError(err, toolsets.failedRemove(key))
    } finally {
      setSaving(null)
    }
  }

  async function handleReveal(key: string) {
    if (revealed[key]) {
      setRevealed(c => withoutKey(c, key))

      return
    }

    try {
      const result = await revealEnvVar(key)
      setRevealed(c => ({ ...c, [key]: result.value }))
    } catch (err) {
      notifyError(err, toolsets.failedReveal(key))
    }
  }

  return {
    saveValue,
    vars,
    rowProps: {
      edits,
      revealed,
      saving,
      testing,
      probeResults,
      setEdits,
      onSave: handleSave,
      onClear: handleClear,
      onReveal: handleReveal,
      onProbe: handleProbe
    }
  }
}

interface CategoryHeadingProps {
  count?: string
  icon: IconComponent
  title: string
}

interface UseEnvCredentials {
  rowProps: Omit<EnvRowProps, 'varKey' | 'info'>
  saveValue: (key: string, value: string) => Promise<{ message?: string; ok: boolean }>
  vars: Record<string, EnvVarInfo> | null
}
