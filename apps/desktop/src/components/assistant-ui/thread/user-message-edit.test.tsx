import { ExportedMessageRepository } from '@assistant-ui/core/internal'
// Clicking a user bubble must open the inline edit composer — through the
// app's incremental external-store runtime (which reimplements capability
// resolution, incl. `edit: onEdit !== undefined`) and the stock runtime.
//
// Note: this covers the React/runtime wiring only. The Electron-level failure
// mode (titlebar -webkit-app-region:drag swallowing clicks on *stuck* sticky
// bubbles) is not reproducible in jsdom — see USER_BUBBLE_BASE_CLASS's no-drag
// carve-out in thread.tsx.
import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useIncrementalExternalStoreRuntime } from '@/lib/incremental-external-store-runtime'

import { Thread } from '.'

const createdAt = new Date('2026-05-01T00:00:00.000Z')

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
  window.setTimeout(() => callback(performance.now()), 0)
)
vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))

Element.prototype.scrollTo = function scrollTo() {}

function stubOffsetDimension(
  prop: 'offsetHeight' | 'offsetWidth',
  clientProp: 'clientHeight' | 'clientWidth',
  fallback: number
) {
  const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop)

  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    get() {
      return previous?.get?.call(this) || (this as HTMLElement)[clientProp] || fallback
    }
  })
}

stubOffsetDimension('offsetWidth', 'clientWidth', 800)
stubOffsetDimension('offsetHeight', 'clientHeight', 600)

function userMessage(): ThreadMessage {
  return {
    id: 'user-1',
    role: 'user',
    content: [{ type: 'text', text: 'edit me please' }],
    attachments: [],
    createdAt,
    metadata: { custom: {} }
  } as ThreadMessage
}

function assistantMessage(): ThreadMessage {
  return {
    id: 'assistant-1',
    role: 'assistant',
    content: [{ type: 'text', text: 'done' }],
    status: { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

// Mirrors chat/index.tsx: incremental runtime + messageRepository + onEdit.
function IncrementalHarness({ onEdit }: { onEdit: () => Promise<void> }) {
  const repository = ExportedMessageRepository.fromArray([userMessage(), assistantMessage()])

  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    messageRepository: repository,
    isRunning: false,
    setMessages: () => {},
    onNew: async () => {},
    onEdit,
    onCancel: async () => {},
    onReload: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  )
}

// Control: stock external store runtime.
function StockHarness({ onEdit }: { onEdit: () => Promise<void> }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [userMessage(), assistantMessage()],
    isRunning: false,
    onNew: async () => {},
    onEdit
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  )
}

// Mirrors chat-surface.tsx's ChatRuntimeBoundary: messageRepository, no
// onEdit/onReload (submission is owned by the injected `submit`).
function ConsoleHarness() {
  const repository = ExportedMessageRepository.fromArray([userMessage(), assistantMessage()])

  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    messageRepository: repository,
    isRunning: false,
    onCancel: async () => {},
    onNew: async () => {},
    onEdit: undefined,
    onReload: undefined
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  )
}

describe('click-to-edit user message', () => {
  it('opens the edit composer with the incremental runtime', async () => {
    const { container } = render(<IncrementalHarness onEdit={async () => {}} />)

    const bubble = await screen.findByRole('button', { name: 'Edit message' })

    fireEvent.click(bubble)

    await waitFor(() => {
      expect(container.querySelector('[data-slot="aui_edit-composer-root"]')).toBeTruthy()
    })
  })

  it('opens the edit composer with the stock runtime', async () => {
    const { container } = render(<StockHarness onEdit={async () => {}} />)

    const bubble = await screen.findByRole('button', { name: 'Edit message' })

    fireEvent.click(bubble)

    await waitFor(() => {
      expect(container.querySelector('[data-slot="aui_edit-composer-root"]')).toBeTruthy()
    })
  })

  // The console shell (`app/console/chat/chat-surface.tsx`) submits through an
  // injected `submit`, so its adapter declares no onEdit/onReload. Before the
  // capability gate, its bubble and refresh buttons still rendered and the
  // vendor threw synchronously — an uncaught renderer error that wiped the
  // transcript the user was looking at.
  it('hides edit and reload when the runtime declares neither', async () => {
    // Scoped to this render: this project does not auto-clean between tests, so
    // `screen` would still see the two harnesses above (both editable).
    const { container } = render(<ConsoleHarness />)
    const inThisThread = within(container)

    expect(inThisThread.queryByRole('button', { name: 'Edit message' })).toBeNull()
    expect(inThisThread.queryByRole('button', { name: 'Refresh' })).toBeNull()

    const bubble = await inThisThread.findByRole('button', { name: /edit me please/ })

    fireEvent.click(bubble)

    expect(container.querySelector('[data-slot="aui_edit-composer-root"]')).toBeNull()
  })
})
