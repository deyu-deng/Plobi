import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, findByText, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { DropdownMenu, DropdownMenuContent } from '@/components/ui/dropdown-menu'
import { $connectedDesktopApps, markConnected } from '@/store/desktop-quotas'
import { $visibleModels } from '@/store/model-visibility'
import { $activeSessionId, $currentModel, $currentProvider } from '@/store/session'

import { ModelMenuPanel } from './model-menu-panel'

// Radix calls these on open; jsdom doesn't implement them.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelOptions = vi.fn()

vi.mock('@/plobi', () => ({
  getGlobalModelOptions: (...args: unknown[]) => getGlobalModelOptions(...args)
}))

/**
 * The backend's catalog as `model.options` returns it: one hub row whose model
 * ids already carry the `<app>/` prefix, plus that row's capabilities.
 */
function catalog(models: string[], capabilities?: Record<string, unknown>) {
  return [
    {
      authenticated: true,
      capabilities,
      models,
      name: 'Local Quota Hub',
      slug: 'aigw',
      total_models: models.length
    }
  ]
}

beforeEach(() => {
  $activeSessionId.set('runtime-1')
  $currentModel.set('')
  $currentProvider.set('')
  $connectedDesktopApps.set({})
  // The visible-model set is persisted; a leftover from another run would hide
  // the row this test is about.
  $visibleModels.set(null)
  getGlobalModelOptions.mockResolvedValue({ providers: [] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderPanel(onSelectModel = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <DropdownMenu open>
        <DropdownMenuContent>
          <ModelMenuPanel onSelectModel={onSelectModel} requestGateway={vi.fn() as never} />
        </DropdownMenuContent>
      </DropdownMenu>
    </QueryClientProvider>
  )

  return onSelectModel
}

describe('ModelMenuPanel — a model that is only waiting', () => {
  it('keeps the row, greys it, and says how long is left', async () => {
    markConnected({ id: 'opencode_api', models: ['big-pickle'], name: 'OpenCode Free' })
    getGlobalModelOptions.mockResolvedValue({
      providers: catalog(['opencode_api/big-pickle'], {
        'opencode_api/big-pickle': {
          unavailable: { reason: 'quota_exhausted', retry_after_seconds: 3600 }
        }
      })
    })

    renderPanel()

    const row = await findByText(document.body, 'Big Pickle')

    // Still listed — a leg that only has to wait must not vanish (user, 11-08 21:3x).
    expect(row).not.toBeNull()
    // The reason and the time left live in the row's tooltip, not on the label.
    // Both come from copy the app already had (the reason word and the time
    // phrase) — this knife adds no new wording.
    const trigger = row.closest('[role="menuitem"]') ?? row
    const note = trigger.getAttribute('title') ?? ''
    expect(note).toContain('used up its quota')
    expect(note).toContain('in 1 h')
    // And the row reads as greyed — the shared SubTrigger has no disabled
    // styling of its own, so the grey is ours.
    expect(trigger.className).toContain('opacity-50')
  })

  it('a greyed row cannot be switched to', async () => {
    markConnected({ id: 'opencode_api', models: ['big-pickle'], name: 'OpenCode Free' })
    getGlobalModelOptions.mockResolvedValue({
      providers: catalog(['opencode_api/big-pickle'], {
        'opencode_api/big-pickle': {
          unavailable: { reason: 'quota_exhausted', retry_after_seconds: 3600 }
        }
      })
    })

    const onSelectModel = renderPanel()

    const row = await findByText(document.body, 'Big Pickle')
    fireEvent.click(row.closest('[role="menuitem"]') ?? row)

    // Radix's `disabled` on a SubTrigger does not stop our own onClick, so the
    // guard has to be ours: clicking must not switch.
    await waitFor(() => {
      expect(onSelectModel).not.toHaveBeenCalled()
    })
  })

  it('a row with no verdict stays pickable', async () => {
    markConnected({ id: 'opencode_api', models: ['big-pickle'], name: 'OpenCode Free' })
    getGlobalModelOptions.mockResolvedValue({
      providers: catalog(['opencode_api/big-pickle'])
    })

    const onSelectModel = renderPanel()

    const row = await findByText(document.body, 'Big Pickle')
    const trigger = row.closest('[role="menuitem"]') ?? row

    expect(trigger.getAttribute('title')).toBeNull()
    expect(trigger.className).not.toContain('opacity-50')

    // The click lands on the row's own trigger — the text span is inside it.
    fireEvent.click(trigger)
    await waitFor(() => {
      // The pick keeps the hub's slug (that is the routing identity) and the
      // joined id the catalog speaks.
      expect(onSelectModel).toHaveBeenCalledWith({ model: 'opencode_api/big-pickle', provider: 'aigw' })
    })
  })
})
