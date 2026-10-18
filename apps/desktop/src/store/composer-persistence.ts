import { atom } from 'nanostores'

import { chatScopeKey, type ChatScope } from '@/app/console/chat/scope'
import { persistBoolean, persistString, storedBoolean, storedString } from '@/lib/storage'
import { updateAtom, type Updater } from '@/lib/store-utils'

// The composer's model/effort/fast is sticky UI state, NOT the profile default
// (that lives in Settings → Model). Persisting it in localStorage makes a pick
// follow across Cmd+N and app restarts instead of snapping back to the default.
// It's deliberately not per-profile (a profile switch force-reseeds to that
// profile's default); the model + provider cells are additionally scoped per
// agent — see the block below, which keys them off the conversation scope.
const COMPOSER_MODEL_KEY = 'plobi.desktop.composer.model'
const COMPOSER_PROVIDER_KEY = 'plobi.desktop.composer.provider'
const COMPOSER_EFFORT_KEY = 'plobi.desktop.composer.reasoning-effort'
const COMPOSER_FAST_KEY = 'plobi.desktop.composer.fast'

// 裁定 44:「一个 Agent 一张嘴 / 习惯是人的属性，不是对话窗口的属性」.
// Which cell the composer's model + provider read and write is a function of the
// conversation scope, so a model picked while talking to one agent does not
// follow you to the next. Inside an L2 project conversation the cell is the
// agent's own — `${base}.<chatScopeKey(scope)>`, i.e. the same identifier the
// conversation store uses (see `app/console/chat/scope.ts`; no second key naming
// system). The L1 secretary and any chat outside a project keep the FLAT keys
// above: existing data is neither moved nor renamed.
//
// `reasoning-effort` / `fast` deliberately still share the one global cell —
// one key rule changes at a time.
let composerModelKey = COMPOSER_MODEL_KEY
let composerProviderKey = COMPOSER_PROVIDER_KEY
let composerScopeIsAgent = false
let composerScopeCell = ''

export const $currentModel = atom(storedString(COMPOSER_MODEL_KEY) ?? '')
export const $currentProvider = atom(storedString(COMPOSER_PROVIDER_KEY) ?? '')
export const $currentReasoningEffort = atom(storedString(COMPOSER_EFFORT_KEY) ?? '')
export const $currentServiceTier = atom('')
export const $currentFastMode = atom(storedBoolean(COMPOSER_FAST_KEY, false))

/** Point the composer's model + provider persistence at one conversation scope's
 *  cell and re-hydrate the two atoms from it. Call it before anything reads or
 *  writes the selection — every setter below funnels through the resolved keys.
 *  `null` / L1 → the flat legacy keys (unchanged data). An empty cell clears the
 *  atoms so the next reseed seeds that person's own default instead of leaving
 *  the previous agent's model on screen. */
export function setComposerModelScope(scope: ChatScope | null | undefined): void {
  const cell = scope && scope.kind === 'agent' ? `.${chatScopeKey(scope)}` : ''

  if (cell === composerScopeCell) {
    return
  }

  composerScopeCell = cell
  composerScopeIsAgent = cell !== ''
  composerModelKey = `${COMPOSER_MODEL_KEY}${cell}`
  composerProviderKey = `${COMPOSER_PROVIDER_KEY}${cell}`

  setCurrentModel(storedString(composerModelKey) ?? '')
  setCurrentProvider(storedString(composerProviderKey) ?? '')
}

/**
 * This agent's OWN persisted pick, or null when it never chose one (then the
 * profile default seeds it). Always null for the flat scope on purpose: the
 * secretary must keep force-reseeding from the profile default exactly as
 * before, so its cell never outranks `force`.
 */
export function storedScopedComposerSelection(): { model: string; provider: string } | null {
  if (!composerScopeIsAgent) {
    return null
  }

  const model = storedString(composerModelKey)

  if (!model) {
    return null
  }

  return { model, provider: storedString(composerProviderKey) ?? '' }
}

export const setCurrentModel = (next: Updater<string>) => {
  updateAtom($currentModel, next)
  persistString(composerModelKey, $currentModel.get() || null)
}

export const setCurrentProvider = (next: Updater<string>) => {
  updateAtom($currentProvider, next)
  persistString(composerProviderKey, $currentProvider.get() || null)
}

export const setCurrentReasoningEffort = (next: Updater<string>) => {
  updateAtom($currentReasoningEffort, next)
  persistString(COMPOSER_EFFORT_KEY, $currentReasoningEffort.get() || null)
}

export const setCurrentServiceTier = (next: Updater<string>) => updateAtom($currentServiceTier, next)

export const setCurrentFastMode = (next: Updater<boolean>) => {
  updateAtom($currentFastMode, next)
  persistBoolean(COMPOSER_FAST_KEY, $currentFastMode.get())
}
