import { act, cleanup, render, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { type MutableRefObject, useEffect, useMemo, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { type QueueEditState } from '@/app/chat/composer/composer-utils'
import { useComposerQueue } from '@/app/chat/composer/hooks/use-composer-queue'
import { useComposerSubmit } from '@/app/chat/composer/hooks/use-composer-submit'
import { agentScopeKey } from '@/app/chat/composer/queue-key'
import type { SubmitOutcome } from '@/app/chat/composer/types'
import { ChatContext, type ChatContextValue } from '@/app/chat/context'
import type { ChatMessage } from '@/lib/chat-messages'
import { type BusyInputMode } from '@/store/busy-input-mode'
import { type ComposerAttachment } from '@/store/composer'
import { clearQueuedPrompts, getQueuedPrompts } from '@/store/composer-queue'

// The composer's send decision tree, on the REAL hooks (`useComposerSubmit` +
// `useComposerQueue`) and the REAL queue store — not a logic mirror.
//
// Why this file exists (user 11-13: 「输入后。发送不了了」): the send path had
// several ways to refuse a message WITHOUT a sound. They are LOCAL refusals
// (the busy gate, the per-session in-flight lock, the session-drift guard) and
// the composer could not tell them from the gateway's rejection — so it
// treated every one of them as "the gateway said no", put the words back in
// the box, and said nothing. The message was neither sent, nor queued, nor
// reported: 「字还在、Agent 没反应、无报错」.
//
// The contract this file pins down:
//   - a LOCAL refusal queues the words (the user SEES them in the queue panel,
//     and they go out on the next drain) instead of bouncing back into the box;
//   - the queue bucket is the **Agent** (`l1` / `agent:<id>`), so it always
//     exists — a key of `null` used to drop the message on the floor;
//   - a GATEWAY rejection still restores them into the box (the error surface
//     already spoke);
//   - what "busy" means is the base's one knob (`display.busy_input_mode`).

const AGENT_A = 'agenda-secretary'
const AGENT_B = 'butler'
const BUCKET_A = `agent:${AGENT_A}`
const BUCKET_B = `agent:${AGENT_B}`
const DRAFT = 'hello there'

interface HarnessApi {
  submitDraft: () => void
  steerDraft: () => void
  loadIntoComposer: (text: string, attachments: ComposerAttachment[]) => void
  stashAt: (scope: string | null, text?: string, attachments?: ComposerAttachment[]) => void
}

type SubmitFn = (
  text: string,
  options?: { attachments?: ComposerAttachment[]; fromQueue?: boolean; scopeKey?: string; steer?: boolean }
) => Promise<SubmitOutcome> | SubmitOutcome

interface ComposerProps {
  busy?: boolean
  /** 忙时怎么办＝底座那一个键（`display.busy_input_mode`）。 */
  busyInputMode?: BusyInputMode
  canSteer?: boolean
  draft: string
  onReady: (api: HarnessApi) => void
  onSteer?: (text: string) => Promise<boolean> | boolean
  onSubmit: SubmitFn
  /** The route the composer sits on — it decides which Agent's bucket is used. */
  pathname?: string
  scopeRef?: MutableRefObject<string | null>
}

// The hooks that need the chat context live INSIDE the provider.
function Composer({
  busy = false,
  busyInputMode = 'interrupt',
  canSteer = false,
  draft,
  onReady,
  onSteer,
  onSubmit,
  pathname = `/agent/${AGENT_A}`,
  scopeRef
}: ComposerProps) {
  const attachmentsAtom = useMemo(() => atom<ComposerAttachment[]>([]), [])
  const draftRef = useRef(draft)
  const editorRef = useRef<HTMLDivElement | null>(null)
  const queueEditRef = useRef<QueueEditState | null>(null)
  const loadIntoComposer = useMemo(() => vi.fn(), [])
  const stashAt = useMemo(() => vi.fn(), [])
  const clearDraft = useMemo(() => vi.fn(), [])
  const focusInput = useMemo(() => vi.fn(), [])
  // The real key derivation — the same one the ChatBar uses: the bucket is the
  // Agent, so it never depends on a session id.
  const activeQueueSessionKey = agentScopeKey(pathname)
  const localScopeRef = useRef<string | null>(activeQueueSessionKey)
  const activeQueueSessionKeyRef = scopeRef ?? localScopeRef

  const queue = useComposerQueue({
    activeQueueSessionKey,
    attachments: [],
    busy,
    clearDraft,
    draftRef,
    focusInput,
    loadIntoComposer,
    onCancel: vi.fn(),
    onSubmit,
    queueEditRef,
    sessionId: null
  })

  const submit = useComposerSubmit({
    activeQueueSessionKey,
    activeQueueSessionKeyRef,
    attachments: [],
    busy,
    busyInputMode,
    canSteer,
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
    onSteer,
    onSubmit,
    queueCurrentDraft: queue.queueCurrentDraft,
    queueEdit: queue.queueEdit,
    queueText: queue.queueText,
    queuedPrompts: queue.queuedPrompts,
    sessionId: null,
    setComposerText: vi.fn(),
    stashAt
  })

  useEffect(() => {
    onReady({
      loadIntoComposer,
      stashAt,
      steerDraft: submit.steerDraft,
      submitDraft: submit.submitDraft
    })
  }, [loadIntoComposer, onReady, stashAt, submit.steerDraft, submit.submitDraft])

  return null
}

function Harness(props: ComposerProps) {
  const contextValue = useMemo<ChatContextValue>(
    () => ({
      attachments: atom<ComposerAttachment[]>([]),
      awaitingInput: false,
      busy: props.busy ?? false,
      disabled: false,
      messages: atom<ChatMessage[]>([]),
      onCancel: vi.fn(),
      onSubmit: props.onSubmit,
      sessionId: null,
      state: {
        model: { canSwitch: false, model: 'test', provider: 'test' },
        tools: { enabled: true, label: 'tools' },
        voice: { enabled: false, active: false }
      }
    }),
    [props.busy, props.onSubmit]
  )

  return (
    <ChatContext.Provider value={contextValue}>
      <Composer {...props} />
    </ChatContext.Provider>
  )
}

describe('composer send — a local refusal must not swallow the message', () => {
  beforeEach(() => {
    clearQueuedPrompts(BUCKET_A)
    clearQueuedPrompts(BUCKET_B)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('queues a message refused locally — the words stay visible instead of bouncing back', async () => {
    // The exact field frame (user 11-13): the session was NOT busy on screen,
    // but the send path refused locally anyway. Before the fix the words went
    // back into the box and nothing else happened — no send, no queue entry,
    // no error. Now they land in the queue, where the user can SEE them.
    const outcomes: SubmitOutcome[] = ['local', 'local']
    const onSubmit = vi.fn(async () => outcomes.shift() ?? 'local')

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} />)

    await act(async () => {
      api!.submitDraft()
    })

    // The words are queued for THIS Agent — the user can see them.
    expect(getQueuedPrompts(BUCKET_A).map(entry => entry.text)).toEqual([DRAFT])

    // ...and they are NOT bounced back into the input box (that was the
    // "nothing happened" tell: the text looked like it had never been sent).
    expect(api!.loadIntoComposer).not.toHaveBeenCalled()
  })

  it('sends the queued message on the next drain (the refusal does not strand it)', async () => {
    const outcomes: SubmitOutcome[] = ['local', true]
    const onSubmit = vi.fn(async () => outcomes.shift() ?? true)

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} />)

    await act(async () => {
      api!.submitDraft()
    })

    // The idle session drains the queue: the message actually goes out.
    await waitFor(() => expect(getQueuedPrompts(BUCKET_A)).toHaveLength(0))
    expect(onSubmit).toHaveBeenCalledWith(DRAFT, expect.objectContaining({ fromQueue: true }))
  })

  it("keeps two Agents' chats apart (a pending message is never misdelivered)", async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => 'local')

    let apiA: HarnessApi | null = null
    let apiB: HarnessApi | null = null

    render(
      <Harness draft={DRAFT} onReady={a => (apiA = a)} onSubmit={onSubmit} pathname={`/agent/${AGENT_A}`} />
    )
    render(
      <Harness draft={DRAFT} onReady={b => (apiB = b)} onSubmit={onSubmit} pathname={`/agent/${AGENT_B}`} />
    )

    await act(async () => {
      apiA!.submitDraft()
    })
    await act(async () => {
      apiB!.submitDraft()
    })

    expect(getQueuedPrompts(BUCKET_A).map(entry => entry.text)).toEqual([DRAFT])
    expect(getQueuedPrompts(BUCKET_B).map(entry => entry.text)).toEqual([DRAFT])
  })

  it('queues a message refused mid-switch under the conversation it was typed in', async () => {
    let resolveSubmit: (outcome: SubmitOutcome) => void = () => {}
    const onSubmit = vi.fn(() => new Promise<SubmitOutcome>(resolve => (resolveSubmit = resolve)))

    const scopeRef: MutableRefObject<string | null> = { current: BUCKET_A }

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} scopeRef={scopeRef} />)

    await act(async () => {
      api!.submitDraft()
    })

    // The user switched to another conversation while the submit was in flight.
    scopeRef.current = BUCKET_B

    await act(async () => {
      resolveSubmit('local')
    })

    // The words belong to the conversation they were typed in — they wait in
    // ITS queue...
    expect(getQueuedPrompts(BUCKET_A).map(entry => entry.text)).toEqual([DRAFT])
    // ...and never in the one now on screen (that was a misdelivery).
    expect(getQueuedPrompts(BUCKET_B)).toEqual([])
    expect(api!.loadIntoComposer).not.toHaveBeenCalled()
  })

  it('leaves no copy of a sent message behind (the bucket never moves, so one send is one send)', async () => {
    const onSubmit = vi.fn(async (_text: string, options?: { fromQueue?: boolean }) => (options?.fromQueue ? true : 'local'))

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} pathname={`/agent/${AGENT_A}`} />)

    await act(async () => {
      api!.submitDraft()
    })

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2))
    // The message was sent — it must not be left in the bucket, or it goes out
    // twice.
    expect(getQueuedPrompts(BUCKET_A)).toEqual([])
  })

  it('still restores a gateway-rejected message into the box (the error surface already spoke)', async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => false)

    let api: HarnessApi | null = null
    render(<Harness draft={DRAFT} onReady={a => (api = a)} onSubmit={onSubmit} />)

    await act(async () => {
      api!.submitDraft()
    })

    expect(api!.loadIntoComposer).toHaveBeenCalledWith(DRAFT, [])
    expect(api!.stashAt).toHaveBeenCalledWith(BUCKET_A, DRAFT, [])
    expect(getQueuedPrompts(BUCKET_A)).toHaveLength(0)
  })
})

// What Enter does while the agent is running is the base's one knob
// (`display.busy_input_mode`): `interrupt`（打断当前这一轮，这句话作为下一轮发
// 出去）／`queue`（排队，等这一轮结束）／`steer`（转向当前这一轮）。桌面端不再
// 自己发明一套——同一个键，CLI／网关／桌面端三处一致。
describe('composer send while busy — the base decides what Enter does', () => {
  beforeEach(() => {
    clearQueuedPrompts(BUCKET_A)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('queues it in `queue` mode (the visible queue panel)', async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => true)

    let api: HarnessApi | null = null
    render(
      <Harness
        busy
        busyInputMode="queue"
        draft={DRAFT}
        onReady={a => (api = a)}
        onSubmit={onSubmit}
      />
    )

    await act(async () => {
      api!.submitDraft()
    })

    expect(getQueuedPrompts(BUCKET_A).map(entry => entry.text)).toEqual([DRAFT])
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('sends it in `interrupt` mode (the base interrupts and takes it as the next turn)', async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => true)

    let api: HarnessApi | null = null
    render(
      <Harness
        busy
        busyInputMode="interrupt"
        draft={DRAFT}
        onReady={a => (api = a)}
        onSubmit={onSubmit}
      />
    )

    await act(async () => {
      api!.submitDraft()
    })

    // The words go out — the desktop does NOT hold them back behind a local
    // busy gate; the base applies the policy (that was the bug: the local gate
    // ate the message before the base ever saw it).
    expect(onSubmit).toHaveBeenCalledWith(DRAFT, expect.objectContaining({ scopeKey: BUCKET_A }))
    expect(getQueuedPrompts(BUCKET_A)).toEqual([])
  })

  it('steers it in `steer` mode, and falls back to the queue when the base refuses', async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => true)
    const onSteer = vi.fn(async () => false)

    let api: HarnessApi | null = null
    render(
      <Harness
        busy
        busyInputMode="steer"
        canSteer
        draft={DRAFT}
        onReady={a => (api = a)}
        onSteer={onSteer}
        onSubmit={onSubmit}
      />
    )

    await act(async () => {
      api!.submitDraft()
    })

    expect(onSteer).toHaveBeenCalledWith(DRAFT)
    // The base refused the steer (no live tool window) ⇒ the words fall back
    // to the queue instead of vanishing.
    expect(getQueuedPrompts(BUCKET_A).map(entry => entry.text)).toEqual([DRAFT])
  })

  it('queues it in `steer` mode when there is nothing to steer with', async () => {
    const onSubmit = vi.fn(async (): Promise<SubmitOutcome> => true)
    const onSteer = vi.fn(async () => true)

    let api: HarnessApi | null = null
    render(
      <Harness
        busy
        busyInputMode="steer"
        canSteer={false}
        draft={DRAFT}
        onReady={a => (api = a)}
        onSteer={onSteer}
        onSubmit={onSubmit}
      />
    )

    await act(async () => {
      api!.submitDraft()
    })

    expect(onSteer).not.toHaveBeenCalled()
    expect(getQueuedPrompts(BUCKET_A).map(entry => entry.text)).toEqual([DRAFT])
  })
})
