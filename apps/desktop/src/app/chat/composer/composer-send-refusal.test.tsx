import { act, cleanup, render, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { type MutableRefObject, useEffect, useMemo, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { type QueueEditState } from '@/app/chat/composer/composer-utils'
import { useComposerQueue } from '@/app/chat/composer/hooks/use-composer-queue'
import { useComposerSubmit } from '@/app/chat/composer/hooks/use-composer-submit'
import type { SubmitOutcome } from '@/app/chat/composer/types'
import { ChatContext, type ChatContextValue } from '@/app/chat/context'
import type { ChatMessage } from '@/lib/chat-messages'
import { type ComposerAttachment } from '@/store/composer'
import { clearQueuedPrompts, getQueuedPrompts } from '@/store/composer-queue'

// The composer's send decision tree, on the REAL hooks (`useComposerSubmit` +
// `useComposerQueue`) and the REAL queue store — not a logic mirror.
//
// Why this file exists (user 11-13: 「输入后。发送不了了」): the send path had
// several ways to refuse a message WITHOUT a sound. Three of them are LOCAL
// refusals (the busy gate, the per-session in-flight lock, the session-drift
// guard) and the composer could not tell them from the gateway's rejection —
// so it treated every one of them as "the gateway said no", put the words back
// in the box, and said nothing. The message was neither sent, nor queued, nor
// reported: 「字还在、Agent 没反应、无报错」.
//
// The contract this file pins down:
//   - a LOCAL refusal queues the words (the user SEES them in the queue panel,
//     and they go out on the next drain) instead of bouncing back into the box;
//   - a DRIFT puts the words back into the session they were typed in — never
//     into the session now on screen;
//   - a GATEWAY rejection still restores them into the box (the error surface
//     already spoke).

const SESSION_KEY = 'stored-session-a'
const OTHER_SESSION_KEY = 'stored-session-b'
const DRAFT = 'hello there'

interface HarnessApi {
  submitDraft: () => void
  loadIntoComposer: (text: string, attachments: ComposerAttachment[]) => void
  stashAt: (scope: string | null, text?: string, attachments?: ComposerAttachment[]) => void
}

type SubmitFn = (
  text: string,
  options?: { attachments?: ComposerAttachment[]; fromQueue?: boolean }
) => Promise<SubmitOutcome> | SubmitOutcome

interface ComposerProps {
  draft: string
  onReady: (api: HarnessApi) => void
  onSubmit: SubmitFn
  scopeRef?: MutableRefObject<string | null>
}

// The hooks that need the chat context live INSIDE the provider.
function Composer({ draft, onReady, onSubmit, scopeRef }: ComposerProps) {
  const attachmentsAtom = useMemo(() => atom<ComposerAttachment[]>([]), [])
  const draftRef = useRef(draft)
  const editorRef = useRef<HTMLDivElement | null>(null)
  const queueEditRef = useRef<QueueEditState | null>(null)
  const loadIntoComposer = useMemo(() => vi.fn(), [])
  const stashAt = useMemo(() => vi.fn(), [])
  const clearDraft = useMemo(() => vi.fn(), [])
  const focusInput = useMemo(() => vi.fn(), [])
  const localScopeRef = useRef<string | null>(SESSION_KEY)
  const activeQueueSessionKeyRef = scopeRef ?? localScopeRef

  const queue = useComposerQueue({
    activeQueueSessionKey: SESSION_KEY,
    attachments: [],
    busy: false,
    clearDraft,
    draftRef,
    focusInput,
    loadIntoComposer,
    onCancel: vi.fn(),
    onSubmit,
    queueEditRef,
    queueSessionKey: SESSION_KEY,
    sessionId: SESSION_KEY
  })

  const submit = useComposerSubmit({
    activeQueueSessionKey: SESSION_KEY,
    activeQueueSessionKeyRef,
    attachments: [],
    busy: false,
    canSteer: false,
    clearDraft,
    disabled: false,
    draftRef,
    drainNextQueued: queue.drainNextQueued,
    editorRef,
    exitQueuedEdit: () => false,
    focusInput,
    inputDisabled: false,
    loadIntoComposer,
    onCancel: vi.fn(),
    onSteer: undefined,
    onSubmit,
    queueCurrentDraft: queue.queueCurrentDraft,
    queueEdit: queue.queueEdit,
    queueText: queue.queueText,
    queuedPrompts: queue.queuedPrompts,
    sessionId: SESSION_KEY,
    setComposerText: vi.fn(),
    stashAt
  })

  useEffect(() => {
    onReady({ submitDraft: submit.submitDraft, loadIntoComposer, stashAt })
  }, [loadIntoComposer, onReady, stashAt, submit.submitDraft])

  return null
}

function Harness(props: ComposerProps) {
  const contextValue = useMemo<ChatContextValue>(
    () => ({
      attachments: atom<ComposerAttachment[]>([]),
      awaitingInput: false,
      busy: false,
      disabled: false,
      messages: atom<ChatMessage[]>([]),
      onCancel: vi.fn(),
      onSubmit: props.onSubmit,
      sessionId: SESSION_KEY,
      state: {
        model: { canSwitch: false, model: 'test', provider: 'test' },
        tools: { enabled: true, label: 'tools' },
        voice: { enabled: false, active: false }
      }
    }),
    [props.onSubmit]
  )

  return (
    <ChatContext.Provider value={contextValue}>
      <Composer {...props} />
    </ChatContext.Provider>
  )
}

describe('composer send — a local refusal must not swallow the message', () => {
  beforeEach(() => {
    clearQueuedPrompts(SESSION_KEY)
    clearQueuedPrompts(OTHER_SESSION_KEY)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('queues a message refused by the busy gate — the words stay visible instead of bouncing back', async () => {
    // The exact field frame (user 11-13): the session was NOT busy on screen,
    // but the send path refused locally anyway. Before the fix the words went
    // back into the box and nothing else happened — no send, no queue entry,
    // no error. Now they land in the queue, where the user can SEE them.
    const outcomes: SubmitOutcome[] = ['busy', 'locked']
    const onSubmit = vi.fn(async () => outcomes.shift() ?? 'locked')

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} />)

    await act(async () => {
      api!.submitDraft()
    })

    // The words are queued for THIS session — the user can see them.
    expect(getQueuedPrompts(SESSION_KEY).map(entry => entry.text)).toEqual([DRAFT])

    // ...and they are NOT bounced back into the input box (that was the
    // "nothing happened" tell: the text looked like it had never been sent).
    expect(api!.loadIntoComposer).not.toHaveBeenCalled()
  })

  it('sends the queued message on the next drain (the refusal does not strand it)', async () => {
    const outcomes: SubmitOutcome[] = ['busy', true]
    const onSubmit = vi.fn(async () => outcomes.shift() ?? true)

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} />)

    await act(async () => {
      api!.submitDraft()
    })

    // The idle session drains the queue: the message actually goes out.
    await waitFor(() => expect(getQueuedPrompts(SESSION_KEY)).toHaveLength(0))
    expect(onSubmit).toHaveBeenCalledWith(DRAFT, expect.objectContaining({ fromQueue: true }))
  })

  it('queues a message refused by the in-flight lock (same visible outcome, no bounce back)', async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => 'locked')

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} />)

    await act(async () => {
      api!.submitDraft()
    })

    expect(getQueuedPrompts(SESSION_KEY).map(entry => entry.text)).toEqual([DRAFT])
    expect(api!.loadIntoComposer).not.toHaveBeenCalled()
  })

  it('puts a drifted message back into the session it was typed in — never into the one on screen', async () => {
    let resolveSubmit: (outcome: SubmitOutcome) => void = () => {}
    const onSubmit = vi.fn(() => new Promise<SubmitOutcome>(resolve => (resolveSubmit = resolve)))

    const scopeRef: MutableRefObject<string | null> = { current: SESSION_KEY }

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} scopeRef={scopeRef} />)

    await act(async () => {
      api!.submitDraft()
    })

    // The user switched to another session while the submit was in flight.
    scopeRef.current = OTHER_SESSION_KEY

    await act(async () => {
      resolveSubmit('drifted')
    })

    // The words go back to where they came from...
    expect(api!.stashAt).toHaveBeenCalledWith(SESSION_KEY, DRAFT, [])
    // ...and NOT into the session now on screen (that was a misdelivery).
    expect(api!.loadIntoComposer).not.toHaveBeenCalled()
  })

  it('still restores a gateway-rejected message into the box (the error surface already spoke)', async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => false)

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} />)

    await act(async () => {
      api!.submitDraft()
    })

    expect(api!.loadIntoComposer).toHaveBeenCalledWith(DRAFT, [])
    expect(api!.stashAt).toHaveBeenCalledWith(SESSION_KEY, DRAFT, [])
    expect(getQueuedPrompts(SESSION_KEY)).toHaveLength(0)
  })
})
