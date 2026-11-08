import { useStore } from '@nanostores/react'

import {
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  dropdownMenuRow,
  dropdownMenuSectionLabel,
  DropdownMenuSeparator,
  DropdownMenuSubContent
} from '@/components/ui/dropdown-menu'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { normalize } from '@/lib/text'
import { setModelPreset } from '@/store/model-presets'
import { notifyError } from '@/store/notifications'
import { $activeSessionId, setCurrentFastMode, setCurrentReasoningEffort } from '@/store/session'

// Plobi' real reasoning levels (see VALID_REASONING_EFFORTS); `none` is owned
// by the Thinking toggle, not the radio.
const EFFORT_OPTIONS = [
  { value: 'minimal', labelKey: 'minimal' },
  { value: 'low', labelKey: 'low' },
  { value: 'medium', labelKey: 'medium' },
  { value: 'high', labelKey: 'high' },
  { value: 'xhigh', labelKey: 'max' }
] as const

/** The levels this model may be set to, as the catalog answered.
 *
 *  A subscription leg names the levels it actually sells (Antigravity's Claude runs at
 *  low / medium / high; Qoder's Qwen3.8-Max at low / medium / xhigh), and a level the
 *  vendor does not have must not sit in his radio group. When the model says nothing at
 *  all the generic ladder stays — "we did not ask" is not "there is no choice".
 *
 *  Exported for the test that pins the two shapes apart.
 */
export function effortChoices(efforts?: readonly string[]): typeof EFFORT_OPTIONS[number][] {
  if (!efforts || efforts.length === 0) {
    return [...EFFORT_OPTIONS]
  }

  const wanted = new Set(efforts.map(effort => effort.trim().toLowerCase()))

  return EFFORT_OPTIONS.filter(option => wanted.has(option.value))
}

/** What the quota hub publishes, per model, about what that model lets him change.
 *
 *  `model.options` returns these keys on the very same object that carries
 *  `fast`/`reasoning`. `ModelCapabilities` in `src/types/plobi.ts` declares only those
 *  two today, and that file is another line's live claim, so the wider shape is read
 *  here — it belongs in the type, and should move there when that window opens.
 */
export interface ModelTuning {
  context?: { adjustable?: boolean; choices?: number[]; default?: number; max?: number }
  default_effort?: string
  efforts?: string[]
  max_output?: number
}

export function tuningOf(capabilities: unknown): ModelTuning {
  return (capabilities ?? {}) as ModelTuning
}

/** How "fast" is achieved for a given model — two different mechanisms:
 *  - `param`: the Anthropic/OpenAI `speed=fast` request parameter.
 *  - `variant`: a separate `…-fast` sibling model selected via the model field.
 */
export type FastControl =
  | { kind: 'none' }
  | { kind: 'param'; on: boolean }
  | { kind: 'variant'; baseId: string; fastId: string; on: boolean }

/** Resolve the fast mechanism for a model: prefer the speed=fast parameter
 *  when the backend supports it, else fall back to a `…-fast` sibling model. */
export function resolveFastControl(
  model: string,
  providerModels: readonly string[],
  paramSupported: boolean,
  currentFastMode: boolean
): FastControl {
  if (paramSupported) {
    return { kind: 'param', on: currentFastMode }
  }

  if (/-fast$/i.test(model)) {
    const baseId = model.replace(/-fast$/i, '')

    // Only a toggle if there's a base to switch back to; otherwise it's a
    // standalone fast model with no "off" state.
    return providerModels.includes(baseId) ? { kind: 'variant', baseId, fastId: model, on: true } : { kind: 'none' }
  }

  const fastId = `${model}-fast`

  if (providerModels.includes(fastId)) {
    return { kind: 'variant', baseId: model, fastId, on: false }
  }

  // Fast isn't natively offered here, but if the session still has the speed
  // param on (carried over from a previous model), expose the toggle so it can
  // be turned off rather than stranded.
  if (currentFastMode) {
    return { kind: 'param', on: true }
  }

  return { kind: 'none' }
}

/** 200000 → "200K", 1000000 → "1M". A value, not a sentence: no copy needed. */
export function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${Number((tokens / 1_000_000).toFixed(1))}M`
  }

  if (tokens >= 1000) {
    return `${Math.round(tokens / 1000)}K`
  }

  return String(tokens)
}

interface ModelEditSubmenuProps {
  /** The windows this model sells (`tuning.context`), when it sells more than one. */
  context?: { adjustable?: boolean; choices?: number[]; default?: number; max?: number }
  /** The window chosen for this model, if any. */
  contextValue?: number
  onContext?: (tokens: number) => void
  /** The vendor's own default level for this model, when it names one. */
  defaultEffort?: string
  /** The levels this model really sells, when the catalog names them. */
  efforts?: readonly string[]
  /** This row's effective reasoning effort (live for the active model, else its
   *  preset) — the submenu shows and edits from this, never the raw session. */
  effort: string
  /** How fast mode is offered for this model (param toggle vs. variant swap). */
  fastControl: FastControl
  /** Whether this row's model is the active one. */
  isActive: boolean
  /** This row's model id — edits persist as its global preset. */
  model: string
  /** Switch to a specific model id (used to swap base ⇄ -fast variant). */
  onSelectModel: (model: string) => Promise<boolean> | void
  /** This row's provider slug — edits persist as its global preset. */
  provider: string
  /** Whether this model supports reasoning effort. */
  reasoning: boolean
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function ModelEditSubmenu({
  defaultEffort,
  efforts,
  effort,
  fastControl,
  isActive,
  model,
  onSelectModel,
  provider,
  reasoning,
  requestGateway,
  context,
  contextValue,
  onContext
}: ModelEditSubmenuProps) {
  const { t } = useI18n()
  const copy = t.shell.modelOptions
  const activeSessionId = useStore($activeSessionId)

  const contextChoices = (context?.adjustable ? context.choices ?? [] : [])
    .filter(choice => Number.isFinite(choice) && choice > 0)
  // The window dial and the thinking levels are two separate knobs. Hanging the dial
  // on `reasoning` hid the window from every model that sells windows but no levels
  // — and the "nothing to change" line swallowed the whole submenu for them.
  const hasContextChoice = contextChoices.length > 1
  const choices = effortChoices(efforts)
  const fallbackEffort = levelForModel(defaultEffort, choices)
  const effortValue = normalizeEffort(effort, choices, fallbackEffort)
  const thinkingOn = isThinkingEnabled(effort)

  // Editing always records the model's global preset; the active model also gets
  // it pushed onto the live session. Non-active edits stay preset-only — they do
  // not switch you to that model.
  const patchReasoning = async (next: string) => {
    setModelPreset(provider, model, { effort: next })

    if (!isActive) {
      return
    }

    setCurrentReasoningEffort(next)

    // Preset-only without a session: `isActive` holds for the global/default
    // row pre-session, and the gateway's `config.set` falls back to global
    // config when none matches — so don't reach it (preset + optimistic store
    // are the whole effect). Same guard in applyModelPreset / toggleFast.
    if (!activeSessionId) {
      return
    }

    try {
      await requestGateway('config.set', { key: 'reasoning', session_id: activeSessionId, value: next })
    } catch (err) {
      setCurrentReasoningEffort(effort)
      setModelPreset(provider, model, { effort })
      notifyError(err, copy.updateFailed)
    }
  }

  const toggleFast = (enabled: boolean) => {
    if (fastControl.kind === 'variant') {
      // Fast is a separate model id. Record the choice on the base model's
      // preset (selectFamily picks the `-fast` sibling later when set), and
      // only swap models now if this is the active row — inactive edits must
      // stay preset-only, same as the param path below.
      setModelPreset(provider, fastControl.baseId, { fast: enabled })

      if (isActive) {
        void onSelectModel(enabled ? fastControl.fastId : fastControl.baseId)
      }

      return
    }

    if (fastControl.kind === 'param') {
      setModelPreset(provider, model, { fast: enabled })

      if (!isActive) {
        return
      }

      setCurrentFastMode(enabled)

      // Preset-only without a session (see patchReasoning).
      if (!activeSessionId) {
        return
      }
      void (async () => {
        try {
          await requestGateway('config.set', {
            key: 'fast',
            session_id: activeSessionId,
            value: enabled ? 'fast' : 'normal'
          })
        } catch (err) {
          setCurrentFastMode(!enabled)
          setModelPreset(provider, model, { fast: !enabled })
          notifyError(err, copy.fastFailed)
        }
      })()
    }
  }

  const hasFast = fastControl.kind !== 'none'
  const fastOn = fastControl.kind === 'none' ? false : fastControl.on

  return (
    <DropdownMenuSubContent className="w-52 p-0" sideOffset={4}>
      {!hasFast && !reasoning && !hasContextChoice ? (
        <div className="px-2.5 py-3 text-xs text-(--ui-text-tertiary)">{copy.noOptions}</div>
      ) : (
        <>
          <DropdownMenuLabel className={dropdownMenuSectionLabel}>{copy.options}</DropdownMenuLabel>
          {reasoning ? (
            <DropdownMenuItem className={dropdownMenuRow} onSelect={event => event.preventDefault()}>
              {copy.thinking}
              <Switch
                checked={thinkingOn}
                className="ml-auto"
                onCheckedChange={checked => void patchReasoning(checked ? effortValue || fallbackEffort : 'none')}
                size="xs"
              />
            </DropdownMenuItem>
          ) : null}
          {hasFast ? (
            <DropdownMenuItem className={dropdownMenuRow} onSelect={event => event.preventDefault()}>
              {copy.fast}
              <Switch checked={fastOn} className="ml-auto" onCheckedChange={toggleFast} size="xs" />
            </DropdownMenuItem>
          ) : null}
          {hasContextChoice ? (
            <>
              {reasoning || hasFast ? <DropdownMenuSeparator className="mx-0" /> : null}
              <DropdownMenuRadioGroup
                onValueChange={value => onContext?.(Number(value))}
                value={String(contextValue ?? context?.default ?? '')}
              >
                {contextChoices.map(choice => (
                  <DropdownMenuRadioItem
                    className={dropdownMenuRow}
                    key={choice}
                    onSelect={event => event.preventDefault()}
                    value={String(choice)}
                  >
                    {formatTokens(choice)}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </>
          ) : null}
          {reasoning ? (
            <>
              <DropdownMenuSeparator className="mx-0" />
              <DropdownMenuLabel className={dropdownMenuSectionLabel}>{copy.effort}</DropdownMenuLabel>
              <DropdownMenuRadioGroup onValueChange={value => void patchReasoning(value)} value={effortValue}>
                {choices.map(option => (
                  <DropdownMenuRadioItem
                    className={dropdownMenuRow}
                    key={option.value}
                    onSelect={event => event.preventDefault()}
                    value={option.value}
                  >
                    {copy[option.labelKey]}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </>
          ) : null}
        </>
      )}
    </DropdownMenuSubContent>
  )
}

function isThinkingEnabled(effort: string): boolean {
  // Empty = Plobi default (medium) = on; only an explicit "none" is off.
  return normalize(effort || 'medium') !== 'none'
}

/** The level to use when nobody chose one: the vendor's own default when it names
 *  one and that default is really on offer, else Plobi's middle, else the middle of
 *  what the model does sell. Never a level this model cannot run. */
export function levelForModel(
  defaultEffort: string | undefined,
  choices: readonly { value: string }[]
): string {
  const wanted = normalize(defaultEffort || '').toLowerCase()

  if (wanted && choices.some(choice => choice.value === wanted)) {
    return wanted
  }

  if (choices.some(choice => choice.value === 'medium')) {
    return 'medium'
  }

  return choices[Math.floor((choices.length - 1) / 2)]?.value ?? ''
}

function normalizeEffort(
  effort: string,
  choices: readonly { value: string }[],
  fallback: string
): string {
  const value = normalize(effort || fallback)

  // Thinking off → no effort selected in the radio group.
  if (value === 'none') {
    return ''
  }

  // A stored preset naming a level this model does not sell shows as unselected
  // rather than pretending to be one the model never offered.
  return choices.some(option => option.value === value) ? value : ''
}
