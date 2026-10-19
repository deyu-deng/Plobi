import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SourcesSettings } from './sources-settings'
import type * as SourcesApi from './sources/api'
import type { SourceRow, SourcesPayload } from './sources/api'

const getSources = vi.fn()
const setSourceEnabled = vi.fn()
const trySource = vi.fn()
const saveSourceSetup = vi.fn()
const notify = vi.fn()
const notifyError = vi.fn()

vi.mock('./sources/api', async () => {
  const actual = await vi.importActual<typeof SourcesApi>('./sources/api')

  return {
    ...actual,
    getSources: () => getSources(),
    saveSourceSetup: (id: string, params: Record<string, string>) => saveSourceSetup(id, params),
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
          fieldHelp: 'help',
          fields: { ics_path: 'Path to the .ics file' },
          saveFailed: 'Could not save those.',
          saveSetup: 'Save these',
          saved: 'Saved',
          setupFailed: 'Could not save the first-time setup.',
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
    setup_fields: [],
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

  // 与后端 `SOURCE_CATEGORIES` 同序。**顺序照闭集，不照字母**——空分类也要出现。
  return {
    categories: ['message', 'social', 'website', 'local', 'system', 'custom'].map(id => ({
      id,
      sources: byCategory.get(id) ?? []
    })),
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
    // 空分类**每一类都出现**（不止 social 一类），所以这里是「至少有一个」而不是「恰好一个」。
    expect(screen.getAllByText('Nothing connected in this category yet.').length).toBeGreaterThan(0)
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

describe('the first-time setup form', () => {
  const ICS = { help: 'Absolute path on this machine.', key: 'ics_path', kind: 'path' as const, label: 'Path to the .ics file', required: true }

  it('draws only the fields the source itself declared', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', state: 'never' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('setup-timetable')).toBeTruthy())
    expect(screen.getByLabelText('Path to the .ics file')).toBeTruthy()
  })

  it('falls back to the declared label for a field nobody translated', async () => {
    // 语言目录没有这一格时用后端给的英文缺省值 —— 第三方包零改动就能用，只是没翻译。
    getSources.mockResolvedValue(payload([
      row({
        category: 'custom',
        enabled: true,
        has_builder: true,
        setup_fields: [{ ...ICS, key: 'third_party_thing', label: 'Their own field' }],
        source: 'timetable',
        state: 'never'
      })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByLabelText('Their own field')).toBeTruthy())
  })

  it('draws no form at all for a source that declares no fields', async () => {
    // 微信首次配置要的是扫码与密钥，两样都不在这一层 —— 所以它就该没有表单。
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByRole('button', { name: /Try once/ })).toBeTruthy())
    expect(screen.queryByTestId('setup-wechat')).toBeNull()
  })

  it('will not save while a required field is empty', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', state: 'never' })
    ]))

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save these' })).toBeTruthy())

    expect((screen.getByRole('button', { name: 'Save these' }) as HTMLButtonElement).disabled).toBe(true)
    expect(saveSourceSetup).not.toHaveBeenCalled()
  })

  it('sends only the filled fields, never an empty string', async () => {
    // 空的**不送**：送一个空串等于把"没填"伪装成"填了空的"。
    getSources.mockResolvedValue(payload([
      row({
        category: 'local',
        enabled: true,
        has_builder: true,
        setup_fields: [ICS, { help: '', key: 'note', kind: 'text', label: 'Note', required: false }],
        source: 'timetable',
        state: 'never'
      })
    ]))
    saveSourceSetup.mockResolvedValue(undefined)

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByLabelText('Path to the .ics file')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Path to the .ics file'), { target: { value: 'D:/x.ics' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save these' }))

    await waitFor(() => expect(saveSourceSetup).toHaveBeenCalledWith('timetable', { ics_path: 'D:/x.ics' }))
  })

  it('re-reads after saving so the row shows the stored value', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', state: 'never' })
    ]))
    saveSourceSetup.mockResolvedValue(undefined)

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByLabelText('Path to the .ics file')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Path to the .ics file'), { target: { value: 'D:/x.ics' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save these' }))

    await waitFor(() => expect(getSources).toHaveBeenCalledTimes(2))
  })

  it('offers no masked input, because the declaration layer cannot declare a secret', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', state: 'never' })
    ]))

    const { container } = render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('setup-timetable')).toBeTruthy())
    expect(container.querySelector('input[type="password"]')).toBeNull()
  })
})
