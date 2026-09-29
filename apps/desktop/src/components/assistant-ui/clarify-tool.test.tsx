import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n'
import type { PlobiGateway } from '@/plobi'
import { $clarifyRequests, setClarifyRequest } from '@/store/clarify'
import { $gateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'

import { ClarifyTool } from './clarify-tool'

// The pending request lives only in the backend's memory, so it is routinely gone
// while this card is still mounted (Stop, or an idle profile backend reaped and
// restarted). The renderer sees the backend's English message and nothing else —
// neither transport carries the JSON-RPC code — so the whole contract is:
// recognise that rejection and retire the card instead of toasting it.
vi.mock('@assistant-ui/react', async importOriginal => {
  const actual = await importOriginal<typeof import('@assistant-ui/react')>()

  return { ...actual, useAuiState: () => true } // selectMessageRunning → the turn is still blocked
})

vi.mock('@/store/notifications', () => ({ notifyError: vi.fn() }))

const copy = TRANSLATIONS.en.assistant.clarify

// The message tui_gateway/server.py:_respond sends for a released clarify (error
// 4028). The numeric code never reaches the renderer, so this is its real shape.
const EXPIRED_MESSAGE = 'no pending answer request'

function mount(outcome: 'expired' | 'other' | 'ok') {
  const request = vi.fn().mockImplementation(() => {
    if (outcome === 'expired') {
      return Promise.reject(new Error(EXPIRED_MESSAGE))
    }

    if (outcome === 'other') {
      return Promise.reject(new Error('gateway went away'))
    }

    return Promise.resolve({ ok: true })
  })

  $gateway.set({ request } as unknown as PlobiGateway)
  setClarifyRequest({ choices: null, question: 'Which file should I read?', requestId: 'req-1', sessionId: null })

  const props = { args: { question: 'Which file should I read?' } } as unknown as ToolCallMessagePartProps

  render(<ClarifyTool {...props} />)

  return request
}

function freeform(): HTMLTextAreaElement {
  return screen.getByPlaceholderText(copy.placeholder) as HTMLTextAreaElement
}

// The exact button labels carry a trailing ⏎ glyph, so match loosely.
function click(label: string) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }))
}

function answerAndContinue(request: ReturnType<typeof vi.fn>) {
  fireEvent.change(freeform(), { target: { value: 'config.yaml' } })
  click(copy.continueLabel)

  return request
}

async function waitForExpiry(request: ReturnType<typeof vi.fn>) {
  await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1))
  await vi.waitFor(() => expect(document.body.textContent).toContain(copy.expired))
}

afterEach(() => {
  cleanup()
  $gateway.set(null)
  $clarifyRequests.set({})
  vi.mocked(notifyError).mockClear()
})

describe('ClarifyTool once the gateway has released the request', () => {
  it('retires the card instead of toasting the backend string', async () => {
    const request = answerAndContinue(mount('expired'))

    await waitForExpiry(request)

    expect(notifyError).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain(EXPIRED_MESSAGE)
  })

  it('disables the input and both buttons, and explains itself in one line', async () => {
    const request = answerAndContinue(mount('expired'))

    await waitForExpiry(request)

    expect(freeform().disabled).toBe(true)

    for (const label of [copy.continueLabel, copy.skip]) {
      const button = screen.getByRole('button', { name: new RegExp(label) }) as HTMLButtonElement

      expect(button.disabled, `${label} must stay dead on an expired card`).toBe(true)
    }

    // The shortcut path is locked with the rest — Enter must not re-arm it.
    fireEvent.keyDown(window, { key: 'Enter' })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('still toasts an unrelated send failure and keeps the card usable', async () => {
    answerAndContinue(mount('other'))

    await vi.waitFor(() => expect(notifyError).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(freeform().disabled).toBe(false))

    expect(document.body.textContent).not.toContain(copy.expired)
  })

  it('clears the parked request on a successful answer, expiry line and all', async () => {
    answerAndContinue(mount('ok'))

    await vi.waitFor(() => expect($clarifyRequests.get()).toEqual({}))

    expect(notifyError).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain(copy.expired)
  })
})

describe('the expiry line', () => {
  it('is translated everywhere and never leaks the internal error', () => {
    for (const [locale, translations] of Object.entries(TRANSLATIONS)) {
      const line = translations.assistant.clarify.expired

      expect(line, `${locale} needs its own expiry line`).toBeTruthy()
      expect(line.toLowerCase(), `${locale} must not echo the backend string`).not.toContain('no pending')
    }
  })
})
