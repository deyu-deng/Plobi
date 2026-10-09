import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  abortDesktopQuota,
  setDesktopQuotaAbort,
  streamDesktopQuotaChat
} from '@/lib/desktop-quota-chat'
import type { DesktopQuotaStreamFrame } from '@/global'

// ---------------------------------------------------------------------------
// The renderer no longer fetches the quota hub itself: a POST with
// Authorization + application/json is preflighted by Chromium, and the hub
// serves no CORS headers, so the request can never reach the model. It goes
// through the main process instead (plobiDesktop.quotaStream) and comes back as
// frames. These tests stub that bridge and pin the contract both sides rely on.
// ---------------------------------------------------------------------------

interface Bridge {
  stream: ReturnType<typeof vi.fn>
  abort: ReturnType<typeof vi.fn>
  frames: Array<(frame: DesktopQuotaStreamFrame) => void>
  unsubscribe: ReturnType<typeof vi.fn>
}

let bridge: Bridge

function emit(frame: DesktopQuotaStreamFrame): void {
  for (const listener of [...bridge.frames]) {
    listener(frame)
  }
}

function sse(obj: unknown): DesktopQuotaStreamFrame {
  return {
    requestId: lastRequestId(),
    kind: 'chunk',
    bytes: new TextEncoder().encode(`data: ${JSON.stringify(obj)}\n\n`)
  }
}

function lastRequestId(): string {
  const call = bridge.stream.mock.calls.at(-1)

  return String(call?.[0] ?? 'unset')
}

function handlers() {
  return {
    appendAssistantDelta: vi.fn(),
    appendReasoningDelta: vi.fn(),
    completeAssistantMessage: vi.fn(),
    failAssistantMessage: vi.fn()
  }
}

function request(extra: Record<string, unknown> = {}) {
  const controller = new AbortController()

  return {
    controller,
    req: {
      baseUrl: 'http://127.0.0.1:8000/v1/',
      model: 'workbuddy/deepseek-chat',
      messages: [{ content: 'hi', role: 'user' as const }],
      sessionId: 's1',
      signal: controller.signal,
      handlers: handlers(),
      ...extra
    }
  }
}

beforeEach(() => {
  bridge = {
    stream: vi.fn(() => Promise.resolve(true)),
    abort: vi.fn(() => Promise.resolve(true)),
    frames: [],
    unsubscribe: vi.fn()
  }

  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: {
      quotaStream: bridge.stream,
      quotaStreamAbort: bridge.abort,
      onQuotaStreamFrame: (cb: (frame: DesktopQuotaStreamFrame) => void) => {
        bridge.frames.push(cb)

        return bridge.unsubscribe
      }
    }
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('streamDesktopQuotaChat over the main-process proxy', () => {
  it('POSTs through the bridge and never carries a credential of its own', async () => {
    const { req } = request()
    const run = streamDesktopQuotaChat(req)

    await vi.waitFor(() => expect(bridge.stream).toHaveBeenCalled())
    emit({ requestId: lastRequestId(), kind: 'status', status: 200, statusText: 'OK' })
    emit(sse({ choices: [{ delta: { content: '你' } }] }))
    emit(sse({ choices: [{ delta: { content: '好' } }] }))
    emit({ requestId: lastRequestId(), kind: 'chunk', bytes: new TextEncoder().encode('data: [DONE]\n\n') })

    await run

    const [requestId, url, body] = bridge.stream.mock.calls[0]

    expect(requestId).toBeTruthy()
    // Trailing slash in baseUrl must not double up, and the hub must be the target.
    expect(url).toBe('http://127.0.0.1:8000/v1/chat/completions')
    expect(body).toMatchObject({ model: 'workbuddy/deepseek-chat', stream: true })
    expect(JSON.stringify(body)).not.toMatch(/sk-|authorization/i)
    expect(req.handlers.appendAssistantDelta.mock.calls.map(c => c[1])).toEqual(['你', '好'])
    expect(req.handlers.completeAssistantMessage).toHaveBeenCalledWith('s1', '你好')
  })

  it('surfaces a non-2xx reply with the hub body', async () => {
    const { req } = request()
    const run = streamDesktopQuotaChat(req)

    await vi.waitFor(() => expect(bridge.stream).toHaveBeenCalled())
    emit({ requestId: lastRequestId(), kind: 'status', status: 400, statusText: 'Bad Request' })
    emit({ requestId: lastRequestId(), kind: 'body', text: 'route does not support tools' })

    await run

    expect(req.handlers.failAssistantMessage).toHaveBeenCalledWith(
      's1',
      expect.stringContaining('400 Bad Request: route does not support tools')
    )
    expect(req.handlers.completeAssistantMessage).not.toHaveBeenCalled()
  })

  it('reads a transport failure as "could not reach the hub", not an empty answer', async () => {
    const { req } = request()
    const run = streamDesktopQuotaChat(req)

    await vi.waitFor(() => expect(bridge.stream).toHaveBeenCalled())
    emit({ requestId: lastRequestId(), kind: 'error', error: 'TypeError: fetch failed' })

    await run

    expect(req.handlers.failAssistantMessage).toHaveBeenCalledWith(
      's1',
      expect.stringContaining('Could not reach the local quota hub.')
    )
    expect(req.handlers.failAssistantMessage.mock.calls[0][1]).toContain('TypeError: fetch failed')
  })

  it('reports an upstream error envelope that arrived inside a 200 stream', async () => {
    const { req } = request()
    const run = streamDesktopQuotaChat(req)

    await vi.waitFor(() => expect(bridge.stream).toHaveBeenCalled())
    emit({ requestId: lastRequestId(), kind: 'status', status: 200, statusText: 'OK' })
    emit(sse({ error: { message: 'User location is not supported' } }))

    await run

    expect(req.handlers.failAssistantMessage).toHaveBeenCalledWith(
      's1',
      'User location is not supported'
    )
  })

  it('keeps partial text when cancelled and tells main to stop the request', async () => {
    const { req, controller } = request()
    const run = streamDesktopQuotaChat(req)

    await vi.waitFor(() => expect(bridge.stream).toHaveBeenCalled())
    emit({ requestId: lastRequestId(), kind: 'status', status: 200, statusText: 'OK' })
    emit(sse({ choices: [{ delta: { content: '半句' } }] }))

    controller.abort()
    await run

    expect(req.handlers.completeAssistantMessage).toHaveBeenCalledWith('s1', '半句')
    expect(bridge.abort).toHaveBeenCalled()
  })

  it('unsubscribes its frame listener once the turn is over', async () => {
    const { req } = request()
    const run = streamDesktopQuotaChat(req)

    await vi.waitFor(() => expect(bridge.stream).toHaveBeenCalled())
    emit({ requestId: lastRequestId(), kind: 'status', status: 200, statusText: 'OK' })
    emit({ requestId: lastRequestId(), kind: 'done' })
    await run

    expect(bridge.unsubscribe).toHaveBeenCalled()
  })

  it('refuses to run outside the desktop shell instead of falling back to a direct fetch', async () => {
    Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: {} })
    const { req } = request()

    await streamDesktopQuotaChat(req)

    expect(req.handlers.failAssistantMessage).toHaveBeenCalledWith(
      's1',
      expect.stringContaining('quota hub proxy is only available inside the desktop shell')
    )
  })
})

describe('abort registry', () => {
  it('aborts a stray controller when a session re-sends', () => {
    const first = new AbortController()
    const second = new AbortController()

    setDesktopQuotaAbort('s1', first)
    setDesktopQuotaAbort('s1', second)
    expect(first.signal.aborted).toBe(true)

    abortDesktopQuota('s1')
    expect(second.signal.aborted).toBe(true)
  })
})
