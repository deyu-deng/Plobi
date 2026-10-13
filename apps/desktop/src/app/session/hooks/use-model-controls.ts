import { type QueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'

import { getGlobalModelInfo } from '@/plobi'
import { type ChatScope } from '@/app/console/chat/scope'
import { translateNow, useI18n } from '@/i18n'
import {
  $connectedDesktopApps,
  firstSelectableQuotaModel,
  isDesktopQuotaProvider,
  quotaSelectionVerdict
} from '@/store/desktop-quotas'
import { notify, notifyError } from '@/store/notifications'
import {
  $activeSessionId,
  $currentModel,
  $currentProvider,
  setCurrentModel,
  setCurrentProvider,
  setComposerModelScope,
  storedScopedComposerSelection
} from '@/store/session'
import type { ModelOptionsResponse } from '@/types/plobi'

interface ModelSelection {
  model: string
  provider: string
}

interface ModelControlsOptions {
  activeSessionId: string | null
  /**
   * True when this conversation belongs to an L2 project 分身 (the controller
   * already derives it from the route — `isL2`). An L2 is its own profile and
   * owns its own `config.yaml`, so a model picked here persists to **that
   * 分身 only** via the base's existing default (`model.persist_switch_by_default`,
   * True). Non-L2 conversations keep the previous session-scoped behaviour.
   */
  isAgentSession?: boolean
  /**
   * The conversation's chat scope (the controller's memoized `chatScope`).
   * 裁定 44: it decides WHICH localStorage cell the selection is sticky in —
   * an agent's own cell inside a project, the flat global cell otherwise. It
   * never decides what gets sent to the backend.
   */
  scope?: ChatScope | null
  queryClient: QueryClient
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function useModelControls({
  activeSessionId,
  isAgentSession = false,
  scope = null,
  queryClient,
  requestGateway
}: ModelControlsOptions) {
  const { t } = useI18n()
  const copy = t.desktop

  const updateModelOptionsCache = useCallback(
    (provider: string, model: string, includeGlobal: boolean) => {
      const patch = (prev: ModelOptionsResponse | undefined) => ({ ...(prev ?? {}), provider, model })

      queryClient.setQueryData<ModelOptionsResponse>(['model-options', activeSessionId || 'global'], patch)

      if (includeGlobal) {
        queryClient.setQueryData<ModelOptionsResponse>(['model-options', 'global'], patch)
      }
    },
    [activeSessionId, queryClient]
  )

  // Seed the composer's model state from the profile default. `force` reseeds
  // for a profile swap (the new profile has its own default) and for an agent
  // swap (that agent's OWN persisted cell is the seed — 裁定 44); otherwise this
  // only fills an EMPTY selection so a user's pick (plain UI state in
  // $currentModel) survives the lifecycle refreshes that fire on boot / fresh
  // draft / session events. A live session owns the footer, so skip entirely.
  const refreshCurrentModel = useCallback(async (force = false) => {
    try {
      // Pin the storage cell for this scope FIRST: every read and write below
      // (and in the early returns) has to land in the same person's cell.
      setComposerModelScope(scope)

      if ($activeSessionId.get()) {
        return
      }

      if (force) {
        // Inside a project the agent's own cell outranks the profile default.
        // Flat scope returns null here, so a profile swap keeps reseeding
        // exactly as it did before.
        const own = storedScopedComposerSelection()

        if (own) {
          setCurrentModel(own.model)
          setCurrentProvider(own.provider)

          return
        }
      }

      if (!force && $currentModel.get()) {
        return
      }

      const result = await getGlobalModelInfo()

      if ($activeSessionId.get() || (!force && $currentModel.get())) {
        return
      }

      if (typeof result.model === 'string') {
        setCurrentModel(result.model)
      }

      if (typeof result.provider === 'string') {
        setCurrentProvider(result.provider)
      }
    } catch {
      // The delayed session.info event still updates this once the agent is ready.
    }
  }, [scope])

  // Returns whether the switch succeeded so callers can await it before applying
  // follow-up changes. The composer model is plain UI state: with no live
  // session it's just stored (and shipped on the next session.create); with one
  // it goes through config.set. Scope follows the base's own default rule
  // (`model.persist_switch_by_default`): an L2 分身 persists the pick into
  // **its own** profile config, so the project keeps the model next time;
  // every other conversation stays session-scoped (`--session`) and never
  // touches the profile default that Settings → Model owns.
  const selectModel = useCallback(
    async (selection: ModelSelection): Promise<boolean> => {
      // Same cell the rest of this scope reads (the controller's scope effect
      // already pinned it; this is the belt-and-braces so a click that beats the
      // effect can never write into another agent's box).
      setComposerModelScope(scope)

      // Desktop-quota providers (Antigravity, …) are not known to the Plobi
      // backend — their chat is routed to the aigw hub instead. Selecting one is
      // pure local UI state: skip the backend config.set so it doesn't 400.
      if (isDesktopQuotaProvider(selection.provider)) {
        setCurrentModel(selection.model)
        setCurrentProvider(selection.provider)
        updateModelOptionsCache(selection.provider, selection.model, !activeSessionId)

        return true
      }

      // Snapshot for rollback: the switch is applied optimistically, so a
      // failure must restore the prior model/provider (store + query cache)
      // rather than leave the UI showing a model the backend never selected.
      const prevModel = $currentModel.get()
      const prevProvider = $currentProvider.get()

      setCurrentModel(selection.model)
      setCurrentProvider(selection.provider)
      updateModelOptionsCache(selection.provider, selection.model, !activeSessionId)

      // No live session yet: the pick is pure UI state. session.create reads
      // $currentModel/$currentProvider and applies it as that session's override.
      if (!activeSessionId) {
        return true
      }

      try {
        // An L2 分身 persists by default (that profile's own config.yaml); every
        // other conversation stays session-scoped with `--session`.
        const scope = isAgentSession ? '' : ' --session'

        await requestGateway('config.set', {
          session_id: activeSessionId,
          key: 'model',
          value: `${selection.model} --provider ${selection.provider}${scope}`
        })

        void queryClient.invalidateQueries({ queryKey: ['model-options', activeSessionId] })

        return true
      } catch (err) {
        setCurrentModel(prevModel)
        setCurrentProvider(prevProvider)
        updateModelOptionsCache(prevProvider, prevModel, !activeSessionId)
        notifyError(err, copy.modelSwitchFailed)

        return false
      }
    },
    [activeSessionId, copy.modelSwitchFailed, isAgentSession, queryClient, requestGateway, scope, updateModelOptionsCache]
  )

  // The hub's verdict can change underneath a conversation that is just sitting
  // open: on this machine an Antigravity sign-in lasts about an hour, and it is
  // renewed by that desktop app, not by Plobi. The shell owns the reading — only the
  // main process may reach the hub, because the renderer's own fetch is preflighted
  // and the hub sends no CORS headers — and it pushes a frame on a slow tick and again
  // whenever the picker asks. So this reacts to each frame instead of polling from
  // here: if the model this composer sits on is no longer offered, land on one that is
  // and say why out loud. A turn that must fail is never the user's bug — the
  // subscription clock ran out, and we know it.
  useEffect(() => {
    let cancelled = false

    const reactToFrame = async () => {
      const provider = $currentProvider.get()
      const model = $currentModel.get()

      // The owner's default-leg ruling (2026-10-12): with nothing chosen yet, the
      // desktop draws on the aggregated subscription quota first — that is the
      // cheaper leg, and the hub has already said it is usable. `DEFAULT_CONFIG`
      // ships `model: ""`, so this fires on a fresh install and never over a pick
      // the user or the profile actually made. A live session owns its own footer,
      // so it is left alone exactly as `refreshCurrentModel` leaves it alone.
      if (!model) {
        if ($activeSessionId.get()) {
          return
        }

        const seed = firstSelectableQuotaModel()

        if (seed) {
          await selectModel(seed)
        }

        return
      }

      const verdict = quotaSelectionVerdict(provider, model)

      if (verdict.state !== 'gone') {
        return
      }

      const to = firstSelectableQuotaModel()

      if (!to || !(await selectModel(to))) {
        return
      }

      if (cancelled) {
        return
      }

      const key = `desktop.quotaReason.${verdict.reason}`
      const translated = translateNow(key)
      // The hub's reason is a machine token (`credential_expired`, `no_tools`, …).
      // A token this build has no copy for still reaches the user as itself: the
      // switch already happened, and hiding the cause is worse than awkward words.
      const reason = translated === key ? translateNow('desktop.quotaReason.unknown', verdict.reason) : translated

      notify({
        kind: 'warning',
        title: translateNow('desktop.quotaFallbackTitle'),
        message: translateNow(
          'desktop.quotaFallbackMessage',
          `${provider}/${model}`,
          `${to.provider}/${to.model}`,
          reason
        )
      })
    }

    // nanostores invokes the listener on subscribe, so mounting this hook evaluates
    // the frame we already have — no first-read gap, no timer of our own.
    const unsubscribe = $connectedDesktopApps.subscribe(() => void reactToFrame())

    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [selectModel])

  return { refreshCurrentModel, selectModel, updateModelOptionsCache }
}
