import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { PlobiGateway } from '@/plobi'

import { useSlashCompletions } from './use-slash-completions'

vi.mock('@/i18n/context', () => ({
  useI18n: () => ({ t: { desktop: { slashCommands: { browseAllSessions: 'Browse all sessions…' } } } })
}))

function gatewayStub(items: { text: string; display?: string; meta?: string }[]) {
  const request = vi.fn(async (method: string) => {
    if (method !== 'complete.slash') {
      throw new Error(`unexpected gateway method: ${method}`)
    }

    return { items, replace_from: 1 }
  })

  return { gateway: { request } as unknown as PlobiGateway, request }
}

type GroupedItem = { metadata?: Record<string, string> }

function groupsOf(items: readonly unknown[]): (string | undefined)[] {
  return (items as readonly GroupedItem[]).map(item => item.metadata?.group)
}

function commandsOf(items: readonly unknown[]): (string | undefined)[] {
  return (items as readonly GroupedItem[]).map(item => item.metadata?.command)
}

describe('useSlashCompletions', () => {
  it('serves the bare "/" from complete.slash and splits skills out of commands', async () => {
    const { gateway, request } = gatewayStub([
      { text: 'help', meta: 'Show commands' },
      { text: 'gif-search', meta: 'Search gifs' },
      // TUI-only command: no desktop surface, so the popover must not offer it.
      { text: 'compact', meta: 'Toggle compact display mode' }
    ])

    const { result } = renderHook(() => useSlashCompletions({ gateway: gateway }))

    // `adapter.search` is optional in the trigger adapter contract; read it off
    // the latest render each call instead of pinning one possibly-stale ref.
    const search = () => result.current.adapter.search!('')

    act(() => {
      search()
    })

    await waitFor(() => expect(search()).toHaveLength(2))

    // The empty query must ride the same completion RPC as a typed prefix — not
    // the old `commands.catalog` detour that never listed skills.
    expect(request).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledWith('complete.slash', { text: '/', limit: 200 })

    const items = search()

    expect(groupsOf(items)).toEqual(['Commands', 'Skills'])
    expect(commandsOf(items)).toEqual(['/help', '/gif-search'])
  })
})
