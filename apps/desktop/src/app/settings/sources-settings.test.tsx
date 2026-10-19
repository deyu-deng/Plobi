import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SourcesSettings } from './sources-settings'
import type * as SourcesApi from './sources/api'
import type { SourceRow, SourcesPayload } from './sources/api'

const getSources = vi.fn()
const setSourceEnabled = vi.fn()
const trySource = vi.fn()
const saveSourceSetup = vi.fn()
const getExternalPackages = vi.fn()
const installExternalPackage = vi.fn()
const removeExternalPackage = vi.fn()
const getActionStatus = vi.fn()
const notify = vi.fn()
const notifyError = vi.fn()

vi.mock('./sources/api', async () => {
  const actual = await vi.importActual<typeof SourcesApi>('./sources/api')

  return {
    ...actual,
    getExternalPackages: () => getExternalPackages(),
    getSources: () => getSources(),
    installExternalPackage: (name: string) => installExternalPackage(name),
    removeExternalPackage: (name: string) => removeExternalPackage(name),
    saveSourceSetup: (id: string, params: Record<string, string>) => saveSourceSetup(id, params),
    setSourceEnabled: (id: string, enabled: boolean) => setSourceEnabled(id, enabled),
    trySource: (id: string) => trySource(id)
  }
})

vi.mock('@/plobi', () => ({
  getActionStatus: (name: string, lines?: number) => getActionStatus(name, lines)
}))

vi.mock('@/store/notifications', () => ({
  notify: (input: unknown) => notify(input),
  notifyError: (err: unknown, fallback: string) => notifyError(err, fallback)
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      agenda: { sources: { dingtalk: 'DingTalk', timetable: 'Timetable', wechat: 'WeChat', zju: 'Academic affairs (ZJU)' } },
      settings: {
        sources: {
          categories: { local: 'Local', message: 'Messaging', social: 'Social media', system: 'System', website: 'Websites', custom: 'Bring your own' },
          disable: 'Stop',
          emptyExternal: 'Could not read what is installed.',
          enable: 'Start',
          fields: { ics_path: 'Path to the .ics file' },
          install: 'Install',
          installFailed: 'That install did not finish.',
          installPlaceholder: 'package name',
          installsForeignCode: "Installing runs that package's own code.",
          installed: 'Installed.',
          installedNone: 'Nothing from outside is installed.',
          loadFailed: 'Could not read the sources.',
          loadingLog: 'Running…',
          noBuilder: 'Broken',
          packageName: 'Package name',
          pillLive: 'collecting',
          pillNoStore: 'store unreadable',
          pillNotStarted: 'never collected',
          pillStale: 'stalled',
          pillSwitchOff: 'off',
          pillUnknown: 'unknown',
          remove: 'Remove',
          removeFailed: 'That removal did not finish.',
          saveFailed: 'Could not save those.',
          saveSetup: 'Save',
          setup: 'Set up',
          setupNeeded: 'Set up',
          switchFailed: 'Could not change the switch.',
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

// 与后端 `SOURCE_CATEGORIES` 同序。**顺序照闭集，不照字母**。
const CATEGORIES = ['message', 'social', 'website', 'local', 'system', 'custom']

function payload(sources: SourceRow[]): SourcesPayload {
  const byCategory = new Map<string, string[]>()

  for (const one of sources) {
    if (!one.category) {
      continue
    }

    byCategory.set(one.category, [...(byCategory.get(one.category) ?? []), one.source])
  }

  return {
    backendIsOlder: false,
    categories: CATEGORIES.map(id => ({ id, sources: byCategory.get(id) ?? [] })),
    checked_at: '2026-10-19T08:15:00',
    sources,
    stale_after_days: 3,
    verdict: 'n/a'
  } as SourcesPayload
}

beforeEach(() => {
  vi.clearAllMocks()
  getExternalPackages.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
})

describe('what the row says', () => {
  it('is a name, a pill, and a button — and nothing else', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, note: '一整段工程说明，还带 ** 星号', source: 'wechat', state: 'live' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getAllByText('WeChat').length).toBeGreaterThan(0))
    expect(screen.getByText('collecting')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy()
    // 后端那句 note **一个字都不上屏**。
    expect(screen.queryByText(/一整段工程说明/)).toBeNull()
    expect(screen.queryByText(/\*\*/)).toBeNull()
  })

  it('shows no timestamp even when the read carries one', async () => {
    getSources.mockResolvedValue(payload([
      row({ age_days: 9.5, enabled: true, has_builder: true, source: 'timetable', state: 'stale' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('stalled')).toBeTruthy())
    expect(screen.queryByText(/9/)).toBeNull()
  })

  it('gives each state its own pill', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: false, source: 'dingtalk', state: 'never' }),
      row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' }),
      row({ enabled: true, has_builder: true, source: 'timetable', state: 'stale' }),
      row({ enabled: true, has_builder: true, source: 'zju', state: 'live' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('off')).toBeTruthy())
    expect(screen.getByText('never collected')).toBeTruthy()
    expect(screen.getByText('stalled')).toBeTruthy()
    expect(screen.getByText('collecting')).toBeTruthy()
  })

  it('does not draw an off switch for a switch it could not read', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: null, has_builder: null, source: 'wechat', state: 'live' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('unknown')).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Start' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
  })

  it('draws no button at all for a defect it cannot fix', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: false, source: 'dingtalk', state: 'never' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('Broken')).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Try once' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Start' })).toBeNull()
  })

  it('offers "Try once" only where §6.1 says so, and warns on the button', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' })]))

    render(<SourcesSettings />)

    const button = await screen.findByRole('button', { name: /Try once/ })

    expect(button.getAttribute('title')).toContain('network')
    fireEvent.click(button)
    await waitFor(() => expect(trySource).toHaveBeenCalledWith('wechat'))
  })

  it('reports a run by the executor’s own words, not a paraphrase', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: true, source: 'dingtalk', state: 'never' })]))
    trySource.mockResolvedValue({
      detail: '注册表说这条在采，但没有任何一方构造得出它的采集器 ⇒ 不会有东西进来',
      ok: false,
      source: 'dingtalk',
      state: 'no_builder'
    })

    render(<SourcesSettings />)
    fireEvent.click(await screen.findByRole('button', { name: /Try once/ }))

    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect((notify.mock.calls.at(-1)?.[0] as { message: string }).message)
      .toBe('注册表说这条在采，但没有任何一方构造得出它的采集器 ⇒ 不会有东西进来')
  })

  it('never prints a raw source id when the display name is missing', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: true, source: 'brand-new-thing', state: 'never' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('Source with no recorded name')).toBeTruthy())
    expect(screen.queryByText('brand-new-thing')).toBeNull()
  })

  it('says "could not read" instead of drawing an empty list', async () => {
    getSources.mockRejectedValue(new Error('offline'))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('sources-failed')).toBeTruthy())
  })
})

describe('what does not get rendered', () => {
  it('hides the empty categories entirely', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('source-group-message')).toBeTruthy())
    expect(screen.queryByTestId('source-group-social')).toBeNull()
    expect(screen.queryByTestId('source-group-system')).toBeNull()
    expect(screen.queryByText('Nothing connected in this category yet.')).toBeNull()
  })

  it('still shows "bring your own" because that one holds an action', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('source-group-custom')).toBeTruthy())
    expect(screen.getByTestId('external-sources')).toBeTruthy()
  })

  it('says "bring your own" is not a category that only says "nothing here"', async () => {
    getSources.mockResolvedValue(payload([]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('external-sources')).toBeTruthy())
    // 装包那一格只留**一句**——上一版是两段散文。
    expect(screen.getByText("Installing runs that package's own code.")).toBeTruthy()
  })
})

describe('the first-time setup form', () => {
  const ICS = { help: 'Absolute path on this machine.', key: 'ics_path', kind: 'path' as const, label: 'Path to the .ics file', required: true }

  it('is collapsed until asked for', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', state: 'never' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Set up' })).toBeTruthy())
    expect(screen.queryByTestId('setup-timetable')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Set up' }))

    await waitFor(() => expect(screen.getByTestId('setup-timetable')).toBeTruthy())
  })

  it('draws no setup button at all for a source that declares no fields', async () => {
    // 微信首次配置要的是扫码与密钥，两样都不在这一层 —— 所以它就该什么都没有。
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: true, source: 'wechat', state: 'never' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByRole('button', { name: /Try once/ })).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Set up' })).toBeNull()
  })

  it('draws only the fields the source itself declared, and falls back to its label', async () => {
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

    fireEvent.click(await screen.findByRole('button', { name: 'Set up' }))
    await waitFor(() => expect(screen.getByLabelText('Their own field')).toBeTruthy())
  })

  it('will not save while a required field is empty', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', state: 'never' })
    ]))

    render(<SourcesSettings />)
    fireEvent.click(await screen.findByRole('button', { name: 'Set up' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy())

    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
    expect(saveSourceSetup).not.toHaveBeenCalled()
  })

  it('sends only the filled fields, never an empty string', async () => {
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
    fireEvent.click(await screen.findByRole('button', { name: 'Set up' }))
    await waitFor(() => expect(screen.getByLabelText('Path to the .ics file')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Path to the .ics file'), { target: { value: 'D:/x.ics' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(saveSourceSetup).toHaveBeenCalledWith('timetable', { ics_path: 'D:/x.ics' }))
  })

  it('offers no masked input, because the declaration layer cannot declare a secret', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', state: 'never' })
    ]))

    const { container } = render(<SourcesSettings />)
    fireEvent.click(await screen.findByRole('button', { name: 'Set up' }))

    await waitFor(() => expect(screen.getByTestId('setup-timetable')).toBeTruthy())
    expect(container.querySelector('input[type="password"]')).toBeNull()
  })
})

describe('bring your own', () => {
  it('will not install an empty name', async () => {
    getSources.mockResolvedValue(payload([]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('external-sources')).toBeTruthy())
    expect((screen.getByRole('button', { name: 'Install' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('sends the name as typed and does not validate it here', async () => {
    // 后端才是权威（它还拒版本约束 / extras / URL）。前端提前拦一半只会让人
    // 以为「我能填的都行」，而真的那句拒绝理由会在日志里。
    getSources.mockResolvedValue(payload([]))
    installExternalPackage.mockResolvedValue('collectors-install-abc')
    getActionStatus.mockResolvedValue({ exit_code: 0, lines: ['装上了'], name: 'x', running: false })

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByTestId('external-sources')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Package name'), { target: { value: 'evil==1.0' } })
    fireEvent.click(screen.getByRole('button', { name: 'Install' }))

    await waitFor(() => expect(installExternalPackage).toHaveBeenCalledWith('evil==1.0'))
  })

  it('shows the log lines the run produced', async () => {
    getSources.mockResolvedValue(payload([]))
    installExternalPackage.mockResolvedValue('collectors-install-abc')
    getActionStatus.mockResolvedValue({
      exit_code: 1,
      lines: ['ERROR: Could not find a version that satisfies the requirement x'],
      name: 'x',
      running: false
    })

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByTestId('external-sources')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Package name'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Install' }))

    // 原样显示，不转述 —— pip 自己说的那句话就是排障用的那句话。
    await waitFor(() =>
      expect(screen.getByTestId('external-log').textContent)
        .toContain('Could not find a version that satisfies the requirement x')
    )
  })

  it('reports a failed run as a failure even though the package list came back fine', async () => {
    getSources.mockResolvedValue(payload([]))
    installExternalPackage.mockResolvedValue('collectors-install-abc')
    getActionStatus.mockResolvedValue({ exit_code: 1, lines: ['boom'], name: 'x', running: false })

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByTestId('external-sources')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Package name'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Install' }))

    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect((notify.mock.calls.at(-1)?.[0] as { kind?: string }).kind).toBe('error')
  })

  it('re-reads the source list afterwards so a newly installed source shows up', async () => {
    getSources.mockResolvedValue(payload([]))
    installExternalPackage.mockResolvedValue('collectors-install-abc')
    getActionStatus.mockResolvedValue({ exit_code: 0, lines: ['ok'], name: 'x', running: false })

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByTestId('external-sources')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Package name'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Install' }))

    // 装完不重读的话，用户看到的是"装成功了但那一条没出现"，而那最容易被读成装失败。
    await waitFor(() => expect(getSources).toHaveBeenCalledTimes(2))
  })

  it('lists an installed package and offers to remove it', async () => {
    getSources.mockResolvedValue(payload([]))
    getExternalPackages.mockResolvedValue([
      { distribution: 'plobi-collector-x', entry_point: 'plobi_collector_x', load_error: null, source: 'x' }
    ])

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('x')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy()
  })

  it('says a package failed to load rather than hiding it', async () => {
    // 装上了但载不进来是一个**读数**，不是"这个包不存在"。
    getSources.mockResolvedValue(payload([]))
    getExternalPackages.mockResolvedValue([
      {
        distribution: 'broken-collector',
        entry_point: 'broken',
        load_error: '这个包装上了但载不进来。',
        source: null
      }
    ])

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('这个包装上了但载不进来。')).toBeTruthy())
  })
})