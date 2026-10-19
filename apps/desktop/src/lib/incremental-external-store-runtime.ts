import {
  AssistantRuntimeImpl,
  BaseAssistantRuntimeCore,
  ExternalStoreThreadListRuntimeCore,
  ExternalStoreThreadRuntimeCore,
  hasUpcomingMessage
} from '@assistant-ui/core/internal'
import {
  type AssistantRuntime,
  type ExternalStoreAdapter,
  generateId,
  type ThreadMessage,
  useRuntimeAdapters
} from '@assistant-ui/react'
import { useEffect, useMemo, useState } from 'react'

const EMPTY_ARRAY = Object.freeze([])

const shallowEqual = (a: object, b: object): boolean => {
  const aKeys = Object.keys(a)

  if (aKeys.length !== Object.keys(b).length) {
    return false
  }

  for (const key of aKeys) {
    if (a[key as keyof typeof a] !== b[key as keyof typeof b]) {
      return false
    }
  }

  return true
}

const getThreadListAdapter = (store: ExternalStoreAdapter) => store.adapters?.threadList ?? {}

function syncRepositoryIncrementally(
  runtime: ExternalStoreThreadRuntimeCore,
  messageRepository: NonNullable<ExternalStoreAdapter['messageRepository']>
): readonly ThreadMessage[] {
  const repository = (runtime as unknown as { repository: ExternalStoreThreadRuntimeCore['repository'] }).repository
  const incomingIds = new Set(messageRepository.messages.map(({ message }) => message.id))

  for (const { message, parentId } of messageRepository.messages) {
    repository.addOrUpdateMessage(parentId, message)
  }

  for (const { message } of repository.export().messages) {
    if (!incomingIds.has(message.id)) {
      repository.deleteMessage(message.id)
    }
  }

  const headId = messageRepository.headId ?? messageRepository.messages.at(-1)?.message.id ?? null

  repository.resetHead(headId)

  return repository.getMessages()
}

export class IncrementalExternalStoreThreadRuntimeCore extends ExternalStoreThreadRuntimeCore {
  override __internal_setAdapter(store: ExternalStoreAdapter): void {
    if (!store.messageRepository) {
      super.__internal_setAdapter(store)

      return
    }

    const self = this as unknown as {
      _assistantOptimisticId: null | string
      _optimisticParentId: null | string | undefined
      _capabilities: object
      _messages: readonly ThreadMessage[]
      _notifyEventSubscribers: (event: string, payload: object) => void
      _notifySubscribers: () => void
      _store?: ExternalStoreAdapter
    }

    if (self._store === store) {
      return
    }

    const isRunning = store.isRunning ?? false
    this.isDisabled = store.isDisabled ?? false

    const oldStore = self._store
    self._store = store

    if (this.extras !== store.extras) {
      this.extras = store.extras
    }

    const newSuggestions = store.suggestions ?? EMPTY_ARRAY

    if (!shallowEqual(this.suggestions, newSuggestions)) {
      this.suggestions = newSuggestions
    }

    const newCapabilities = {
      switchToBranch: store.setMessages !== undefined,
      switchBranchDuringRun: false,
      edit: store.onEdit !== undefined,
      reload: store.onReload !== undefined,
      cancel: store.onCancel !== undefined,
      speech: store.adapters?.speech !== undefined,
      dictation: store.adapters?.dictation !== undefined,
      voice: store.adapters?.voice !== undefined,
      unstable_copy: store.unstable_capabilities?.copy !== false,
      attachments: !!store.adapters?.attachments,
      feedback: !!store.adapters?.feedback,
      queue: false
    }

    if (!shallowEqual(self._capabilities, newCapabilities)) {
      self._capabilities = newCapabilities
    }

    if (oldStore && oldStore.isRunning === store.isRunning && oldStore.messageRepository === store.messageRepository) {
      self._notifySubscribers()

      return
    }

    if (self._assistantOptimisticId) {
      this.repository.deleteMessage(self._assistantOptimisticId)
      self._assistantOptimisticId = null
    }

    const messages = syncRepositoryIncrementally(this, store.messageRepository)

    if (messages.length > 0) {
      this.ensureInitialized()
    }

    if ((oldStore?.isRunning ?? false) !== (store.isRunning ?? false)) {
      self._notifyEventSubscribers(store.isRunning ? 'runStart' : 'runEnd', {})
    }

    if (hasUpcomingMessage(isRunning, messages)) {
      // Mirror ExternalStoreThreadRuntimeCore's own optimistic insert (core
      // 0.3.22, external-store-thread-runtime-core.js:274-287): a stable id
      // per parentId plus a message normalized to the full ThreadMessage
      // shape — status 'running' and the complete metadata block that
      // fromThreadMessageLike would produce. The hand-rolled
      // addOrUpdateMessage shipped in v2026.10.19 skipped that
      // normalization and left the most recent assistant message WITHOUT
      // metadata; BaseThreadRuntimeCore.state reads
      // `metadata.unstable_state` on the most recent assistant message on
      // every render, so any thread running at startup threw straight into
      // the root error boundary. (appendOptimisticMessage does NOT exist in
      // core 0.3.22 — only in the unrelated 0.1.17 copy — and
      // fromThreadMessageLike is runtime-exported but missing from
      // internal.d.ts, so the normalization is inlined here.)
      const parentId = messages.at(-1)?.id ?? null

      if (!self._assistantOptimisticId || self._optimisticParentId !== parentId) {
        self._assistantOptimisticId = generateId()
        self._optimisticParentId = parentId
      }

      this.repository.addOrUpdateMessage(parentId, {
        id: self._assistantOptimisticId,
        role: 'assistant',
        content: [],
        createdAt: new Date(),
        status: { type: 'running' },
        metadata: {
          unstable_state: null,
          unstable_annotations: [],
          unstable_data: [],
          steps: [],
          custom: {}
        }
      })
    }

    this.repository.resetHead(self._assistantOptimisticId ?? messages.at(-1)?.id ?? null)
    self._messages = this.repository.getMessages()
    self._notifySubscribers()
  }
}

class IncrementalExternalStoreRuntimeCore extends BaseAssistantRuntimeCore {
  threads: ExternalStoreThreadListRuntimeCore

  constructor(adapter: ExternalStoreAdapter) {
    super()

    this.threads = new ExternalStoreThreadListRuntimeCore(
      getThreadListAdapter(adapter),
      () => new IncrementalExternalStoreThreadRuntimeCore(this._contextProvider, adapter)
    )
  }

  setAdapter(adapter: ExternalStoreAdapter): void {
    this.threads.__internal_setAdapter(getThreadListAdapter(adapter))
    this.threads.getMainThreadRuntimeCore().__internal_setAdapter(adapter)
  }
}

export function useIncrementalExternalStoreRuntime<T extends ThreadMessage>(
  store: ExternalStoreAdapter<T>
): AssistantRuntime {
  const [runtime] = useState(() => new IncrementalExternalStoreRuntimeCore(store as ExternalStoreAdapter))

  useEffect(() => {
    runtime.setAdapter(store as ExternalStoreAdapter)
  })

  const { modelContext } = useRuntimeAdapters() ?? {}

  useEffect(() => {
    if (!modelContext) {
      return undefined
    }

    return runtime.registerModelContextProvider(modelContext)
  }, [modelContext, runtime])

  return useMemo(() => new AssistantRuntimeImpl(runtime), [runtime])
}
