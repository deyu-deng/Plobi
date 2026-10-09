import type { DesktopQuotaStreamFrame } from '@/global'
import type { ChatMessage } from '@/lib/chat-messages'
import { chatMessageText } from '@/lib/chat-messages'

// ---------------------------------------------------------------------------
// Desktop-quota chat streaming client
//
// When the selected provider is a connected desktop-quota app (Antigravity,
// Cursor, Workbuddy), the chat submit pipeline bypasses the Plobi backend's
// `prompt.submit` and instead streams directly from the local aigw hub — an
// OpenAI-compatible gateway that aggregates the closed-source desktop apps'
// subscription quotas into one `http://127.0.0.1:<port>/v1` endpoint.
//
// This module owns:
//   1. The SSE streaming client that POSTs to aigw `/chat/completions` and
//      feeds text/reasoning deltas into the SAME message-stream mutators the
//      gateway event handler uses (so the assistant bubble renders identically
//      to a normal backend turn).
//   2. A per-session AbortController registry so `cancelRun` can abort an
//      in-flight aigw stream the same way it interrupts a backend turn.
//
// Honest boundary (v1): desktop-quota turns are text-only. File/image
// attachments are NOT forwarded to aigw (the composer still shows them, but
// only the textual context reaches the model). Tool calls are not supported —
// the response is plain text + optional reasoning. The assistant turn is NOT
// persisted to the Plobi backend's session transcript (it lives in renderer
// state only), so a session resume after the app restarts won't replay it.
// ---------------------------------------------------------------------------

/** Mutators borrowed from useMessageStream — same signatures, same sessionId keying. */
export interface DesktopQuotaStreamHandlers {
  appendAssistantDelta: (sessionId: string, delta: string) => void
  appendReasoningDelta: (sessionId: string, delta: string) => void
  completeAssistantMessage: (sessionId: string, text: string) => void
  failAssistantMessage: (sessionId: string, error: string) => void
}

export interface DesktopQuotaChatRequest {
  /** aigw OpenAI-compatible base URL, e.g. http://127.0.0.1:8019/v1 */
  baseUrl: string
  /** Full served model id WITH the provider prefix, e.g. antigravity/gemini-3-pro */
  model: string
  /** OpenAI-format messages (system/user/assistant). */
  messages: { content: string; role: 'assistant' | 'system' | 'user' }[]
  /** Runtime session id — keys the message-stream mutators. */
  sessionId: string
  /** Aborts the stream (wired to cancelRun). */
  signal: AbortSignal
  handlers: DesktopQuotaStreamHandlers
}

/** Active AbortControllers keyed by runtime session id. */
const activeStreams = new Map<string, AbortController>()

/** Register an in-flight stream so cancelRun can abort it. */
export function setDesktopQuotaAbort(sessionId: string, controller: AbortController): void {
  const prev = activeStreams.get(sessionId)

  // Best-effort abort a stray controller (e.g. a re-entrant send) without
  // throwing — it may already be settled.
  prev?.abort()

  activeStreams.set(sessionId, controller)
}

/** Abort + clear the in-flight desktop-quota stream for a session (if any). */
export function abortDesktopQuota(sessionId: string): void {
  const controller = activeStreams.get(sessionId)

  if (controller) {
    controller.abort()
    activeStreams.delete(sessionId)
  }
}

/**
 * Build the OpenAI messages array for aigw from the live transcript + the new
 * user text. Visible user/assistant messages are mapped to their text content;
 * hidden, errored, pending, and tool-only messages are skipped. The latest
 * user message (already seeded optimistically by the submit pipeline) carries
 * the new prompt text, so it is included as-is.
 */
export function buildDesktopQuotaMessages(
  transcript: ChatMessage[],
  newUserText: string
): { content: string; role: 'assistant' | 'system' | 'user' }[] {
  const out: { content: string; role: 'assistant' | 'system' | 'user' }[] = []

  for (const message of transcript) {
    if (message.hidden || message.error) {
      continue
    }

    if (message.role !== 'user' && message.role !== 'assistant') {
      continue
    }

    // Skip a pending assistant placeholder (no content yet).
    if (message.role === 'assistant' && message.pending && !chatMessageText(message).trim()) {
      continue
    }

    const text = chatMessageText(message).trim()

    if (!text) {
      continue
    }

    out.push({ content: text, role: message.role })
  }

  // If the optimistic user message didn't make it into the transcript snapshot
  // (e.g. a brand-new session whose state hasn't flushed to $messages yet),
  // append the new text as the final user turn so aigw always sees the prompt.
  const last = out.at(-1)

  if (!last || last.role !== 'user' || last.content !== newUserText.trim()) {
    out.push({ content: newUserText.trim(), role: 'user' })
  }

  return out
}

interface QuotaProxiedReply {
  ok: boolean
  status: number
  statusText: string
  /** Present when the hub answered without a stream (a non-2xx body). */
  text: string
  body: ReadableStream<Uint8Array> | null
  /** Unsubscribes the frame listener. Must run once the stream is finished. */
  dispose: () => void
}

let streamSeq = 0

/**
 * POST to the local quota hub *through the main process*.
 *
 * A browser-origin POST with `Authorization` + `application/json` is not a
 * CORS-"simple" request, so Chromium preflights it; the hub has no CORS layer,
 * so the preflight can never pass and the turn dies before a token is spent.
 * The main process has no same-origin policy and already holds the hub key, so
 * it makes the request and forwards the raw bytes back as frames.
 */
async function quotaStreamRequest(
  url: string,
  payload: unknown,
  signal: AbortSignal
): Promise<QuotaProxiedReply> {
  const { quotaStream, quotaStreamAbort, onQuotaStreamFrame } = window.plobiDesktop ?? {}

  if (!quotaStream || !quotaStreamAbort || !onQuotaStreamFrame) {
    throw new Error('quota hub proxy is only available inside the desktop shell')
  }

  const requestId = `quota-${Date.now()}-${(streamSeq += 1)}`
  const pending: Array<DesktopQuotaStreamFrame> = []
  let wake: (() => void) | null = null
  let ended = false

  const unsubscribe = onQuotaStreamFrame(frame => {
    if (frame.requestId !== requestId || ended) {
      return
    }

    pending.push(frame)
    wake?.()
  })

  const take = async (): Promise<DesktopQuotaStreamFrame | null> => {
    while (!pending.length) {
      if (ended) {
        return null
      }

      await new Promise<void>(resolve => {
        wake = resolve
      })
    }

    return pending.shift() ?? null
  }

  // Declared as arrow consts (not `function`) so the narrowed bridge members
  // stay non-optional inside their bodies.
  const onAbort = (): void => {
    void quotaStreamAbort(requestId)
    dispose()
  }

  signal.addEventListener('abort', onAbort)

  const dispose = (): void => {
    if (ended) {
      return
    }

    ended = true
    wake?.()
    unsubscribe()
    signal.removeEventListener('abort', onAbort)
  }

  void quotaStream(requestId, url, payload)

  const first = await take()

  if (!first || first.kind === 'error') {
    dispose()
    throw new Error(first?.error ?? 'quota hub request failed before answering')
  }

  const status = first.status ?? 0
  const statusText = first.statusText ?? ''
  const ok = status >= 200 && status < 300

  if (!ok) {
    const bodyFrame = await take()
    const text = bodyFrame?.text ?? ''

    dispose()

    return { ok, status, statusText, text, body: null, dispose }
  }

  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      const frame = await take()

      if (!frame || frame.kind === 'done') {
        controller.close()
      } else if (frame.kind === 'chunk' && frame.bytes) {
        controller.enqueue(frame.bytes)
      } else if (frame.kind === 'error') {
        controller.error(new Error(frame.error ?? 'quota hub stream failed'))
      } else {
        controller.close()
      }
    },
    cancel() {
      void quotaStreamAbort(requestId)
      dispose()
    }
  })

  return { ok, status, statusText, text: '', body, dispose }
}

/**
 * Stream a desktop-quota chat completion from the aigw hub. Resolves once the
 * stream finishes (the assistant message has been finalized via the handlers).
 * Rejects only on a non-abort network/parse failure (the handlers' fail path
 * already runs, so the caller's catch is for bookkeeping like releasing the
 * busy lock).
 */
export async function streamDesktopQuotaChat(req: DesktopQuotaChatRequest): Promise<void> {
  const { baseUrl, model, messages, sessionId, signal, handlers } = req

  const url = `${baseUrl.replace(/\/+$/, '')}/chat/completions`

  let response: QuotaProxiedReply

  try {
    response = await quotaStreamRequest(
      url,
      { model, messages, stream: true },
      signal
    )
  } catch (err) {
    if (signal.aborted) {
      return
    }

    handlers.failAssistantMessage(sessionId, friendlyAigwError(err, 'Could not reach the local quota hub.'))

    return
  }

  if (!response.ok) {
    handlers.failAssistantMessage(
      sessionId,
      `Quota hub returned ${response.status} ${response.statusText}${response.text ? `: ${response.text.slice(0, 300)}` : ''}`
    )
    response.dispose()

    return
  }

  if (!response.body) {
    handlers.failAssistantMessage(sessionId, 'Quota hub returned an empty stream.')
    response.dispose()

    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let assistantText = ''
  let reasoningText = ''
  let sawAnyChunk = false

  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read()

      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })

      // SSE frames are separated by a blank line. Process complete frames only
      // so a delta split across chunks isn't parsed prematurely.
      const frames = buffer.split('\n\n')

      buffer = frames.pop() ?? ''

      for (const frame of frames) {
        const delta = parseSseFrame(frame)

        if (delta === null) {
          // [DONE] sentinel — stream is finished.
          handlers.completeAssistantMessage(sessionId, assistantText)

          return
        }

        // The aigw hub forwards upstream failures (e.g. Google Code Assist's
        // "User location is not supported" region lock) as a
        // `data: {"error": {"message": ...}}` SSE frame with HTTP 200 — the
        // generator raised inside the stream, so the server can't change the
        // status code. Surface it instead of silently completing an empty turn.
        if (delta.error) {
          handlers.failAssistantMessage(sessionId, delta.error)

          return
        }

        if (delta.content) {
          sawAnyChunk = true
          assistantText += delta.content
          handlers.appendAssistantDelta(sessionId, delta.content)
        }

        if (delta.reasoning) {
          sawAnyChunk = true
          reasoningText += delta.reasoning
          handlers.appendReasoningDelta(sessionId, delta.reasoning)
        }
      }
    }

    // Stream ended without an explicit [DONE] (some gateways close the body
    // instead). Finalize with whatever text we accumulated.
    if (sawAnyChunk || assistantText) {
      handlers.completeAssistantMessage(sessionId, assistantText)
    } else {
      handlers.failAssistantMessage(sessionId, 'Quota hub stream ended with no content.')
    }
  } catch (err) {
    if (signal.aborted) {
      // Abort mid-stream: finalize the partial text so the bubble keeps what
      // arrived (mirrors cancelRun's finalize for backend turns).
      handlers.completeAssistantMessage(sessionId, assistantText)

      return
    }

    handlers.failAssistantMessage(sessionId, friendlyAigwError(err, 'Quota hub stream failed.'))
  } finally {
    // The frame listener is per-request; leaving it subscribed would leak a
    // listener (and a requestId) for every turn the app runs.
    response.dispose()
  }
}

interface ParsedDelta {
  content: string
  reasoning: string
  error?: string
}

/** Returns null for the [DONE] sentinel, otherwise the concatenated delta. */
function parseSseFrame(frame: string): ParsedDelta | null {
  const lines = frame.split('\n')
  let dataLine = ''

  for (const line of lines) {
    const trimmed = line.trimStart()

    if (trimmed.startsWith('data:')) {
      dataLine += trimmed.slice(5).trimStart()
    }
  }

  if (!dataLine) {
    return { content: '', reasoning: '' }
  }

  if (dataLine.trim() === '[DONE]') {
    return null
  }

  try {
    const parsed = JSON.parse(dataLine) as {
      error?: { message?: string }
      choices?: Array<{
        delta?: {
          content?: string
          reasoning?: string
          reasoning_content?: string
        }
      }>
    }

    // Upstream error envelope forwarded by the aigw hub (see main.py:_sse).
    if (parsed.error && typeof parsed.error.message === 'string') {
      return { content: '', reasoning: '', error: parsed.error.message }
    }

    const delta = parsed.choices?.[0]?.delta ?? {}

    return {
      content: typeof delta.content === 'string' ? delta.content : '',
      reasoning:
        typeof delta.reasoning === 'string'
          ? delta.reasoning
          : typeof delta.reasoning_content === 'string'
            ? delta.reasoning_content
            : ''
    }
  } catch {
    // A malformed JSON chunk (rare; some gateways emit keepalive comments).
    // Skip it rather than failing the whole stream.
    return { content: '', reasoning: '' }
  }
}

function friendlyAigwError(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) {
    return `${fallback} (${err.message})`
  }

  return fallback
}
