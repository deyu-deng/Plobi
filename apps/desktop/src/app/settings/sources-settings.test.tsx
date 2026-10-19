import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SourcesSettings } from './sources-settings'
import type * as SourcesApi from './sources/api'
import type { SourceRow, SourcesPayload } from './sources/api'

const getSources = vi.fn()
const setSourceEnabled = vi.fn()
const trySource = vi.fn()
const notify = vi.fn()
const notifyError = vi.fn()

vi.mock('./sources/api', async () => {
  const actual = await vi.importActual<typeof SourcesApi>('./sources/api')

  return {
    ...actual,
    getSources: () => getSources(),
    setSourceEnabled: (id: string, enabled: boolean) => setSourceEnabled(id, enabled),
    trySource: (id: string) => trySource(id)
  }
})

vi.mock('@/store/notifications', () => ({
  notify: (input: unknown) => notify(input),
  notifyError: (err: unknown, fallback: string) => notifyError(err, fallback)
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      agenda: { sources: { wechat: 'WeChat', zju: 'Academic affairs (ZJU)' } },
      settings: {
        sources: {
          categories: { local: 'Local', message: 'Messaging', social: 'Social media', system: 'System', website: 'Websites', custom: 'Bring your own' },
          disable: 'Turn off',
          emptyCategory: 'Nothing connected in this category yet.',
          enable: 'Turn on',
          lastWrite: (count: number) => `Last new entry ${count} days ago.`,
          lead: 'lead',
          loadFailed: 'Could not read the sources.',
          noBuilder: 'Registered as collecting, but nothing can construct its collector. This is a defect on our side, not a setting you can change.',
          stateUnknown: 'State not recorded — not guessing.',
          switchFailed: 'Could not change the switch.',
          switchOff: 'The collect switch is off.',
          switchUnreadable: 'Could not read the collect switch.',
          title: 'Sources',
          tryFailed: 'That run did not come back.',
          tryOnce: 'Try once',
          tryRunsForReal: 'Runs a real collection — it reaches the network and the disk.',
          tryRunning: 'Running…',
          unknownSource: 'Source with no recorded name'
        }
      }
    }
  })
}))

function row(over: Partial<SourceRow> = {}): SourceRow {
  return {
    age_days: null,
    category: 'message',
    collecting: null,
    enabled: null,
    enabled_origin: null,
    events: 0,
    has_builder: null,
    last_created_at: '',
    note: '',
    requires_manual_setup: null,
    setup: null,
    source: 'x',
    state: 'unknown',
    touched_after_last_write: false,
    ...over
  }
}

function payload(sources: SourceRow[]): SourcesPayload {
  const byCategory = new Map<string, string[]>()

  for (const one of sources) {
    if (!one.category) {
      continue
    }

    byCategory.set(one.category, [...(byCategory.get(one.category) ?? []), one.source])
  }

  return {
    categories: [
      { id: 'message', sources: byCategory.get('message') ?? [] },
      { id: 'social', sources: byCategory.get('social') ?? [] }
    ],
    checked_at: '2026-10-19T08:15:00',
    sources,
    stale_after_days: 3,
    verdict: 'n/a'
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
})

describe('the sources panel', () => {
  it('says "could not read" instead of drawing an empty list', async () => {
    getSources.mockRejectedValue(new Error('offline'))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('sources-failed')).toBeTruthy())
    expect(screen.queryByTestId('source-group-message')).toBeNull()
  })

  it('shows a category with nothing in it rather than hiding the category', async () => {
    getSources.mockResolvedValue(payload([row({ source: 'wechat' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('source-group-social')).toBeTruthy())
    expect(screen.getByText('Nothing connected in this category yet.')).toBeTruthy()
  })

  it('shows the collector’s own note verbatim', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, note: 'zju：服务器要求人工验证码，已停手', source: 'zju', state: 'stale' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('zju：服务器要求人工验证码，已停手')).toBeTruthy())
  })

  it('offers "Try once" only where §6.1 says so', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' })
    ]))

    render(<SourcesSettings />)

    const button = await screen.findByRole('button', { name: /Try once/ })

    fireEvent.click(button)
    await waitFor(() => expect(trySource).toHaveBeenCalledWith('wechat'))
  })

  it('warns on the button that trying really runs a collection', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' })
    ]))

    render(<SourcesSettings />)

    // 纪律「不降级」：代价必须写在按钮上，不能让用户以为只是刷新一下。
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Try once/ }).getAttribute('title')).toContain('network')
    )
  })

  it('reports a run by the executor’s own words, not a paraphrase', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, source: 'dingtalk', state: 'never' })
    ]))
    trySource.mockResolvedValue({
      detail: '注册表说这条在采，但没有任何一方构造得出它的采集器 ⇒ 不会有东西进来',
      ok: false,
      source: 'dingtalk',
      state: 'no_builder'
    })

    render(<SourcesSettings />)
    fireEvent.click(await screen.findByRole('button', { name: /Try once/ }))

    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(notify.mock.calls[0][0].message)
      .toBe('注册表说这条在采，但没有任何一方构造得出它的采集器 ⇒ 不会有东西进来')
  })

  it('draws no button at all for a defect it cannot fix', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: false, source: 'dingtalk', state: 'never' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText(/Registered as collecting/)).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Try once/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Turn on/ })).toBeNull()
  })

  it('does not draw an off switch for a switch it could not read', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: null, has_builder: null, source: 'wechat', state: 'live' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('Could not read the collect switch.')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Turn on/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Turn off/ })).toBeNull()
  })

  it('offers "Turn on" for a genuinely off switch', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: false, has_builder: null, source: 'dingtalk', state: 'never' })
    ]))
    setSourceEnabled.mockResolvedValue(undefined)

    render(<SourcesSettings />)
    fireEvent.click(await screen.findByRole('button', { name: 'Turn on' }))

    await waitFor(() => expect(setSourceEnabled).toHaveBeenCalledWith('dingtalk', true))
  })

  it('never prints a raw source id when the display name is missing', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, source: 'brand-new-thing', state: 'never' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('Source with no recorded name')).toBeTruthy())
    expect(screen.queryByText('brand-new-thing')).toBeNull()
  })
})