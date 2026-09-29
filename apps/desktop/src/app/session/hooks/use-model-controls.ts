import { type QueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import { getGlobalModelInfo } from '@/plobi'
import { useI18n } from '@/i18n'
import { notifyError } from '@/store/notifications'
import { $activeSessionId, $currentModel, $currentProvider, setCurrentModel, setCurrentProvider } from '@/store/session'
import { isDesktopQuotaProvider } from '@/store/desktop-quotas'
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
  queryClient: QueryClient
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function useModelControls({
  activeSessionId,
  isAgentSession = false,
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
  // for a profile swap (the new profile has its own default); otherwise this
  // only fills an EMPTY selection so a user's pick (plain UI state in
  // $currentModel) survives the lifecycle refreshes that fire on boot / fresh
  // draft / session events. A live session owns the footer, so skip entirely.
  const refreshCurrentModel = useCallback(async (force = false) => {
    try {
      if ($activeSessionId.get()) {
        return
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
  }, [])

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
    [activeSessionId, copy.modelSwitchFailed, isAgentSession, queryClient, requestGateway, updateModelOptionsCache]
  )

  return { refreshCurrentModel, selectModel, updateModelOptionsCache }
}
