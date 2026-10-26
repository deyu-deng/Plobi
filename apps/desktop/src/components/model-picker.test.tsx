import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connectedDesktopApps, markConnected } from '@/store/desktop-quotas'
import type { ModelOptionProvider } from '@/types/plobi'

import { ModelPickerDialog } from './model-picker'

// cmdk / Radix reach for these; jsdom doesn't ship them.
class FakeResizeObserver {
  disconnect(): void {}
  observe(): void {}
  unobserve(): void {}
}
globalThis.ResizeObserver ??= FakeResizeObserver as unknown as typeof ResizeObserver

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

// Two legs of one software. `quotaProviders` clones the hub row once per app, and
// every clone keeps the HUB's slug — that is the routing identity a pick must keep.
// Which means any key built off `slug` (or off the humanized `name`, same brand by
// design) collides, and React then unmounts one subtree and mounts the other on
// every render: the picker re-fires its mount effect and asks the hub again. That
// self-sustaining loop is the 2026-10-26 "一打开模型选择框整个聊天框就一直闪".
const catalog: ModelOptionProvider[] = [
  { models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' },
  { models: ['workbuddy/hy3', 'workbuddy_api/hy3'], name: 'Local Quota Hub', slug: 'aigw' }
]

function gatewayReturning(providers: ModelOptionProvider[]) {
  return {
    request: vi.fn(async () => ({ providers }))
  } as never
}

async function renderPicker(providers: ModelOptionProvider[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const gw = gatewayReturning(providers)
  render(
    <QueryClientProvider client={client}>
      <ModelPickerDialog
        contentClassName=""
        currentModel="workbuddy/hy3"
        currentProvider="aigw"
        gw={gw}
        onOpenChange={() => {}}
        onSelect={() => {}}
        open
        sessionId="s-1"
      />
    </QueryClientProvider>
  )
  // Radix DialogContent renders through a portal, so the rows live on
  // document.body rather than in the container testing-library returned. Waiting on
  // the rows (not on the header copy, which already names the current model) is what
  // proves the catalog actually landed before we count groups.
  await waitFor(() => expect(document.querySelectorAll('[cmdk-item]').length).toBeGreaterThan(0))
  return document.body
}

describe('ModelPickerDialog group identity', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
    markConnected({ id: 'workbuddy', models: ['hy3'], name: 'WorkBuddy' })
    markConnected({ id: 'workbuddy_api', models: ['hy3'], name: 'WorkBuddy' })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders both same-slug hub legs without a duplicate React key', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})

    const root = await renderPicker(catalog)

    const headings = [...root.querySelectorAll('[cmdk-group-heading]')]
    const workbuddy = headings.filter(h => (h.textContent ?? '').includes('WorkBuddy'))

    expect(workbuddy).toHaveLength(2)
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/two children with the same key/)
  })

  it('keys the model rows by the row identity, not the shared hub slug', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})

    const root = await renderPicker(catalog)

    // Every row is a cmdk item; two legs × one model = two rows, and they must not
    // collide on cmdk's own `value` identity either.
    const items = [...root.querySelectorAll('[cmdk-item]')]

    expect(items).toHaveLength(3) // zai's glm-5.2 + the two hub legs
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/two children with the same key/)
  })
})
