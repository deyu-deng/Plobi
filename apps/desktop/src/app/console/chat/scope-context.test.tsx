import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setGatewayState } from '@/store/session'

vi.mock('@/components/assistant-ui/thread', () => ({ Thread: () => <div data-testid="thread" /> }))

import { useChatContext, type ChatContextValue } from '../../chat/context'
import { ChatSurface } from './chat-surface'
import { L1_SCOPE } from './scope'
import { ScopeChatContext, type ScopeComposerActions } from './scope-context'

/**
 * Guards the L1/L2 attach surface. The composer's "+" button is gated only on
 * `state.tools.enabled` (`chat/composer/context-menu.tsx:54`), which the console
 * hardcodes true — but EVERY item inside it is gated on its own callback
 * (`:67-82`), and a pasted image is only attached when `onAttachImageBlob` is
 * supplied (`chat/composer/index.tsx:324-370`). So when the console context
 * stopped short of those keys, the user saw a normal-looking "+" whose contents
 * were all grey and a paste that silently did nothing.
 */
const ATTACH_KEYS = [
  'onAddContextRef',
  'onAttachDroppedItems',
  'onAttachImageBlob',
  'onPasteClipboardImage',
  'onPickFiles',
  'onPickFolders',
  'onPickImages'
] as const

/** The controller's `useComposerActions` return, sliced down to what the composer attaches with. */
function makeComposerActions(): ScopeComposerActions {
  return {
    addContextRefAttachment: vi.fn(),
    attachDroppedItems: vi.fn().mockResolvedValue(true),
    attachImageBlob: vi.fn().mockResolvedValue(true),
    pasteClipboardImage: vi.fn().mockResolvedValue(true),
    pickContextPaths: vi.fn().mockResolvedValue(undefined),
    pickImages: vi.fn().mockResolvedValue(undefined)
  }
}

/** Surfaces the context value the provider built, without rendering the composer. */
function Probe({ onValue }: { onValue: (value: ChatContextValue) => void }) {
  const value = useChatContext()

  useEffect(() => {
    onValue(value)
  })

  return null
}

describe('ScopeChatContext — composer attach surface', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setGatewayState('open')
  })

  afterEach(() => {
    cleanup()
    setGatewayState('idle')
  })

  it('wires every attachment callback once the shell injects its composer actions', () => {
    let value: ChatContextValue | undefined

    render(
      <ScopeChatContext composer={makeComposerActions()}>
        <Probe onValue={next => (value = next)} />
      </ScopeChatContext>
    )

    expect(value).toBeTruthy()
    // The button was always visible — that is what made the gap look like a
    // working feature rather than a missing one.
    expect(value!.state.tools.enabled).toBe(true)
    expect(value!.cwd).toBeDefined()

    for (const key of ATTACH_KEYS) {
      expect(typeof value![key], `${key} must be a function`).toBe('function')
    }
  })

  it('leaves the attachment callbacks undefined without the injected actions', () => {
    let value: ChatContextValue | undefined

    render(
      <ScopeChatContext>
        <Probe onValue={next => (value = next)} />
      </ScopeChatContext>
    )

    for (const key of ATTACH_KEYS) {
      expect(value![key]).toBeUndefined()
    }
  })

  it('binds the kind into onPickFiles / onPickFolders instead of handing pickContextPaths the menu event', async () => {
    const composer = makeComposerActions()
    let value: ChatContextValue | undefined

    render(
      <ScopeChatContext composer={composer}>
        <Probe onValue={next => (value = next)} />
      </ScopeChatContext>
    )

    value!.onPickFiles?.()
    value!.onPickFolders?.()
    value!.onPickImages?.()

    expect(composer.pickContextPaths).toHaveBeenCalledWith('file')
    expect(composer.pickContextPaths).toHaveBeenCalledWith('folder')
    expect(composer.pickImages).toHaveBeenCalled()
  })

  it('threads the actions through ChatSurface into the real ChatBar: pasting an image attaches it', async () => {
    const composer = makeComposerActions()

    render(
      <MemoryRouter>
        <ChatSurface composer={composer} scope={L1_SCOPE} submit={vi.fn().mockResolvedValue(true)} />
      </MemoryRouter>
    )

    const blob = new Blob(['not-a-real-png'], { type: 'image/png' })
    const editor = screen.getByRole('textbox', { name: 'Message' })

    fireEvent.paste(editor, {
      clipboardData: {
        items: [{ kind: 'file', type: 'image/png', getAsFile: () => blob }],
        files: [],
        getData: () => ''
      }
    })

    await waitFor(() => expect(composer.attachImageBlob).toHaveBeenCalledWith(blob))
  })
})
