import { CompositeContextProvider } from '@assistant-ui/core/internal'
import { type ThreadMessage } from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'

import { IncrementalExternalStoreThreadRuntimeCore } from './incremental-external-store-runtime'

const createdAt = new Date('2026-05-01T00:00:00.000Z')

function userMessage(): ThreadMessage {
  return {
    id: 'user-1',
    role: 'user',
    content: [{ type: 'text', text: 'hello' }],
    attachments: [],
    createdAt,
    metadata: { custom: {} }
  } as ThreadMessage
}

function runningStore() {
  return {
    isRunning: true,
    messageRepository: {
      messages: [{ message: userMessage(), parentId: null }],
      headId: 'user-1'
    }
  } as never
}

describe('IncrementalExternalStoreThreadRuntimeCore optimistic placeholder', () => {
  it('inserts a fully-normalized assistant placeholder while a thread is running', () => {
    const core = new IncrementalExternalStoreThreadRuntimeCore(
      new CompositeContextProvider(),
      runningStore()
    )

    const messages = core.messages
    const placeholder = messages.at(-1)

    expect(placeholder?.role).toBe('assistant')
    // Regression guard for the v2026.10.19 release crash: the hand-rolled
    // addOrUpdateMessage produced an assistant message WITHOUT metadata, and
    // BaseThreadRuntimeCore.state reads `metadata.unstable_state` on the
    // most recent assistant message on every render — the release build fell
    // into the root error boundary as soon as a thread was running.
    expect(placeholder?.metadata).toBeDefined()
    expect(placeholder?.metadata?.unstable_state).toBeNull()
    expect(placeholder?.status).toEqual({ type: 'running' })
  })

  it('keeps a stable optimistic id while the parent message is unchanged', () => {
    const store = runningStore()
    const core = new IncrementalExternalStoreThreadRuntimeCore(new CompositeContextProvider(), store)

    const firstId = core.messages.at(-1)?.id
    core.__internal_setAdapter(store)
    const secondId = core.messages.at(-1)?.id

    expect(firstId).toBeTruthy()
    expect(secondId).toBe(firstId)
  })

  it('exposes a readable state getter (the v2026.10.19 crash site)', () => {
    const core = new IncrementalExternalStoreThreadRuntimeCore(
      new CompositeContextProvider(),
      runningStore()
    )

    expect(() => core.state).not.toThrow()
    expect(core.state).toBeNull()
  })
})
