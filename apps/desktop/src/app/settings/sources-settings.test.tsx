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

// 名单那一块自己的测试在它档里（`talker-collection.test.tsx`）；这一档只钉"它挂在哪一行上"，
// 所以打桩替掉它——不然它自己的 i18n 取键会把这一档整片带崩（那是另一档的契约，不是这里的）。
vi.mock('@/app/agenda/talker/talker-collection', () => ({
  TalkerCollection: () => <div data-testid="talker-panel-stub" />
}))

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
          categories: { local: 'Local', message: 'Messaging', social: 'Social media', system: 'System', website: 'Websites', email: 'Mailboxes', custom: 'Bring your own' },
          emptyExternal: 'Could not read what is installed.',
          fields: { cadence: 'Collection cadence', ics_path: 'Path to the .ics file' },
          install: 'Install',
          installFailed: 'That install did not finish.',
          installPlaceholder: 'package name',
          installsForeignCode: "Installing runs that package's own code.",
          installed: 'Installed.',
          installedNone: 'Nothing from outside is installed.',
          loadFailed: 'Could not read the sources.',
          loadingLog: 'Running…',
          packageName: 'Package name',
          remove: 'Remove',
          removeFailed: 'That removal did not finish.',
          saveFailed: 'Could not save those.',
          saveSetup: 'Save',
          sidecarUp: 'Collection service started',
          sidecarNotUp: 'Switch is on, but the collection service did not come up',
          sidecarDown: 'Collection service stopped',
          sidecarNotDown: 'Switch is off, but the collection service is still answering',
          switchFailed: 'Could not change the switch.',
          title: 'Sources',
          tryFailed: 'That run did not come back.',
          tryRunsForReal: 'Runs a real collection — it reaches the network and the disk.',
          tryRunning: 'Running…',
          statusConnected: 'Connected',
          statusDisconnected: 'Not connected',
          statusError: 'Connection problem',
          drawerEvery: 'Frequency',
          timeJustNow: 'Just now',
          timeMinutesAgo: '{n} min ago',
          timeHoursAgo: '{n} h ago',
          timeDaysAgo: '{n} d ago',
          timeMinutesIn: 'in {n} min',
          timeHoursIn: 'in {n} h',
          timeDaysIn: 'in {n} d',
          drawerLastRun: 'Last run',
          drawerNextRun: 'Next run',
          drawerNeverRun: 'Never ran',
          drawerNextEveryRound: 'Every round',
          drawerPoolCount: 'In the pool',
          drawerPoolUnreadable: 'Pool unreadable',
          drawerAgendaCount: 'In the agenda',
          syncNow: 'Sync now',
          cadenceModeEveryRound: 'Every round',
          cadenceModeEveryHours: 'Every few hours',
          cadenceModeDaily: 'Daily',
          cadenceModeWeekly: 'Weekly',
          cadenceEveryHours: 'Every {n} hours',
          cadenceAdvanced: 'Custom expression',
          cadenceWeekdays: { '0': 'Sun', '1': 'Mon', '2': 'Tue', '3': 'Wed', '4': 'Thu', '5': 'Fri', '6': 'Sat' },
          cadenceCustom: 'Custom',
          cadencePresets: { per_round: 'Follow every round', hourly: 'Hourly', four_hours: 'Every 4 hours', six_hours: 'Every 6 hours', daily: 'Daily at 03:00', weekly: 'Weekly on Monday at 03:00' },
          unknownSource: 'Source with no recorded name',
          reasons: {
            captcha_required: 'Manual verification required',
            client_not_logged_in: 'Its client app has never signed in on this machine',
            internal: 'The local collector is not ready',
            live: 'Last collection succeeded',
            never_ran: 'No collection recorded yet',
            off: 'Turned off manually',
            unknown_state: 'This collection result is not supported yet',
          },
          reasonUnknown: 'This collection result is not supported yet',
          drawerReason: 'Reason',
          drawerRunNote: 'Last run message',
          drawerProbe: 'Local prerequisite'
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
const CATEGORIES = ['message', 'social', 'website', 'local', 'system', 'email', 'custom']

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

/** 行上唯一的控件：Radix 那颗开关是 `role="switch"`，标题那颗是 `role="button"`。 */
function switches(container: HTMLElement) {
  return container.querySelectorAll('[role="switch"]')
}

describe('what the row says', () => {
  it('is a name, one switch — and no text buttons at all', async () => {
    // 用户 10-22：「一个信源就一个开关按钮……不要有这么多按钮」。旧版这里最多三颗
    // 文字按钮（首次配置 / 试一次 / 停掉），这一条把它们**逐颗**钉回不存在。
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, note: '一整段工程说明，还带 ** 星号', source: 'wechat', state: 'live', status: 'connected' })
    ]))

    const { container } = render(<SourcesSettings />)

    await waitFor(() => expect(switches(container).length).toBe(1))
    // 名字出现两次不奇怪：可见的那一处 + 开关的无障碍名（读屏要听得出"这是微信的开关"）。
    expect(screen.getAllByText('WeChat').length).toBeGreaterThan(0)
    expect(screen.getByText('Connected')).toBeTruthy()

    for (const label of ['Stop', 'Start', 'Set up', 'Try once']) {
      expect(screen.queryByRole('button', { name: label })).toBeNull()
    }

    // 后端那句 note 一个字都不上屏（这条是 10-19 立的，没因为改版松过）。
    expect(screen.queryByText(/一整段工程说明/)).toBeNull()
    expect(container.textContent).not.toMatch(/\*\*/)
  })

  it('draws the three states, and only the error one carries a reason', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: false, source: 'dingtalk', status: 'disconnected' }),
      row({ enabled: true, has_builder: true, source: 'wechat', status: 'connected' }),
      row({
        enabled: true,
        has_builder: true,
        source: 'zju',
        status: 'error',
        status_code: 'captcha_required',
        status_detail: '要人工验证码：这一轮按口径停手',
      })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('Not connected')).toBeTruthy())
    expect(screen.getByText('Connected')).toBeTruthy()
    expect(screen.getByText('Connection problem')).toBeTruthy()
    // 出错那一行多的是**目录里那句人话**；执行方逐字原话要点开抽屉才出现（用户 10-22 拍的这一格）。
    expect(screen.getByText('Manual verification required')).toBeTruthy()
    expect(screen.queryByText('要人工验证码：这一轮按口径停手')).toBeNull()
    // 正常态什么都不多写，未连接也不多说一句。
    expect(screen.queryByText('Never ran')).toBeNull()
    expect(screen.queryByText('Last run')).toBeNull()
    expect(screen.queryByText('Turned off manually')).toBeNull()
    expect(screen.queryByText('Last collection succeeded')).toBeNull()
  })

  it('does not draw an off switch for a switch it could not read', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: null, has_builder: null, source: 'wechat', status: 'disconnected' })]))

    const { container } = render(<SourcesSettings />)

    await waitFor(() => expect(switches(container).length).toBe(1))

    expect((switches(container)[0] as HTMLButtonElement).disabled).toBe(true)
    expect(switches(container)[0].getAttribute('data-state')).toBe('unchecked')
  })

  it('opens the drawer on the row, with the readings that used to be invisible', async () => {
    // 旧屏问不到"这条到底采到东西没有"：行上那个数读的是日程库，池里 371 条显示成 0。
    // 上一次/下一次都按**现在**算：写死一个时刻的夹具过一天就从「in 4 h」变成「4 h ago」，
    // 而那句断言钉的是"绝对时刻不上屏、屏上说多久以前/以后"，不是某一个日期。
    const lastRun = new Date(Date.now() - 2 * 86_400_000).toISOString()
    const nextRun = new Date(Date.now() + 4 * 3_600_000).toISOString()

    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, last_run_at: lastRun, next_run_at: nextRun, pool_count: 371, pool_kinds: { class: 65, major: 238 }, source: 'zdbk', status: 'connected', status_code: 'live' }),
      // 关着的一条：它没有"下一次"，所以那一格写破折号，不写「跟着每一轮」（那句话对一条停用的源是假的）。
      // 关着的一条：它没有“下一次”，那一格写破折号（那句话对停用的源是假的）。
      row({ enabled: false, source: 'timetable', status: 'disconnected', status_code: 'off' }),
      // 按会话准入的那一条：名单挂在它自己那一行下面（画不画由后端那句声明决定）。
      row({ conversation_scoped: true, enabled: true, has_builder: true, source: 'wechat', status: 'connected' })
    ]))

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByText('Source with no recorded name')).toBeTruthy())

    expect(screen.queryByTestId('source-drawer-zdbk')).toBeNull()
    fireEvent.click(screen.getByText('Source with no recorded name'))

    await waitFor(() => expect(screen.getByTestId('source-drawer-zdbk')).toBeTruthy())
    const drawer = screen.getByTestId('source-drawer-zdbk').textContent ?? ''
    // 用户 10-23：「下一次（显示多久之前，不要显示一个时间）」⇒ 绝对时刻**不上屏**。
    expect(drawer).not.toContain(lastRun)
    expect(drawer).not.toContain(nextRun)
    expect(drawer).toMatch(/(h|d) ago/)
    expect(drawer).toMatch(/in \d+ (h|min|d)/)
    // 通用那几格还在：原因、频率；池里/日程里压成一行小字。
    expect(drawer).toContain('Reason')
    expect(drawer).toContain('Last collection succeeded')
    expect(drawer).toContain('Frequency')
    expect(drawer).toContain('Every round')
    expect(drawer).toContain('371')
    expect(drawer).toContain('class 65')

    fireEvent.click(screen.getByText('Timetable'))

    await waitFor(() => expect(screen.getByTestId('source-drawer-timetable')).toBeTruthy())
    // 关着的一条没有"下一次"：那一格是破折号（频率那一格照旧说它跟着每一轮）。
    expect(screen.getByTestId('source-drawer-timetable').textContent).toContain('—')
    // 名单只跟着“按会话准入”的那一条：没声明的那两条一块都不画。
    expect(screen.queryByTestId('source-talkers-zdbk')).toBeNull()
    expect(screen.queryByTestId('source-talkers-timetable')).toBeNull()

    // 名字在屏上出现两处（可见标题 + 开关的无障碍名），点标题那一处。
    fireEvent.click(screen.getAllByText('WeChat')[0])

    await waitFor(() => expect(screen.getByTestId('source-talkers-wechat')).toBeTruthy())
    expect(screen.getByTestId('talker-panel-stub')).toBeTruthy()
  })

  it('keeps the executor\'s own sentence one click away, not on the row', async () => {
    // 出错那一行给人话（目录里查的那句），逐字原话收进抽屉——两个都要，但位置不同。
    getSources.mockResolvedValue(payload([
      row({
        enabled: true,
        source: 'dingtalk',
        status: 'error',
        status_code: 'internal',
        status_detail: '注册表说这条在采，但没有任何一方构造得出它的采集器',
      })
    ]))

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByText('DingTalk')).toBeTruthy())

    expect(screen.getByText('The local collector is not ready')).toBeTruthy()
    expect(screen.queryByText(/没有任何一方构造得出/)).toBeNull()

    fireEvent.click(screen.getByText('DingTalk'))

    await waitFor(() => expect(screen.getByTestId('source-drawer-dingtalk')).toBeTruthy())
    expect(screen.getByTestId('source-run-note-dingtalk').textContent)
      .toContain('注册表说这条在采，但没有任何一方构造得出它的采集器')
    expect(screen.getByTestId('source-drawer-dingtalk').textContent).toContain('The local collector is not ready')
  })

  it('sends the click to the switch only when the source is configured', async () => {
    setSourceEnabled.mockResolvedValue(undefined)
    getSources.mockResolvedValue(payload([
      row({ enabled: false, has_builder: true, source: 'wechat', status: 'disconnected' })
    ]))

    const { container } = render(<SourcesSettings />)
    await waitFor(() => expect(switches(container).length).toBe(1))
    fireEvent.click(switches(container)[0])

    await waitFor(() => expect(setSourceEnabled).toHaveBeenCalledWith('wechat', true))
    // 没有本机服务的信源：开关之后**不多说话**（用户 10-23 要的是"这条有进程就说进程"，不是每条都弹一句）。
    expect(notify).not.toHaveBeenCalled()
  })

  it('says whether the own service of a source actually came up', async () => {
    // 用户 10-23：「微信那个按钮形同虚设。我打开还是关闭都是显示未连接」⇒ 开关现在会真的
    // 拉起/收掉那条自己的服务，而屏上要说得出**它到底活没活**（后端回读数，句子在四语目录里）。
    setSourceEnabled.mockResolvedValue({ action: 'started_not_listening', detail: '后端那句原文',
                                         listening: false, port: 5030 })
    getSources.mockResolvedValue(payload([
      row({ enabled: false, has_builder: true, source: 'wechat', status: 'disconnected' })
    ]))

    const { container } = render(<SourcesSettings />)
    await waitFor(() => expect(switches(container).length).toBe(1))
    fireEvent.click(switches(container)[0])

    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error',
      message: 'Switch is on, but the collection service did not come up'
    })))
    expect(screen.queryByText('后端那句原文')).toBeNull()
  })

  it('guides an unconfigured source into the form instead of flipping a dead switch', async () => {
    // 「第一次点击就引导配置跳转到相应的界面」。一颗"拨上去什么也不会发生"的开关
    // 是这一屏最容易骗人的形状：用户会以为已经在采了。
    const ICS = { help: '', key: 'ics_path', kind: 'path' as const, label: 'Path to the .ics file', required: true }

    getSources.mockResolvedValue(payload([
      row({ category: 'local', enabled: false, has_builder: true, setup: {}, setup_fields: [ICS], source: 'timetable', status: 'disconnected' })
    ]))

    const { container } = render(<SourcesSettings />)
    await waitFor(() => expect(switches(container).length).toBe(1))
    fireEvent.click(switches(container)[0])

    expect(setSourceEnabled).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByTestId('source-drawer-timetable')).toBeTruthy())
    expect(document.activeElement).toBe(screen.getByLabelText('Path to the .ics file'))
  })

  it('syncs from inside the drawer, by the executor’s own words', async () => {
    getSources.mockResolvedValue(payload([
      row({ enabled: true, has_builder: true, source: 'dingtalk', status: 'disconnected' })
    ]))
    trySource.mockResolvedValue({
      detail: '注册表说这条在采，但没有任何一方构造得出它的采集器 ⇒ 不会有东西进来',
      ok: false,
      source: 'dingtalk',
      state: 'no_builder'
    })

    render(<SourcesSettings />)
    fireEvent.click(await screen.findByText('DingTalk'))
    fireEvent.click(await screen.findByRole('button', { name: 'Sync now' }))

    await waitFor(() => expect(trySource).toHaveBeenCalledWith('dingtalk'))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect((notify.mock.calls.at(-1)?.[0] as { message: string }).message)
      .toBe('注册表说这条在采，但没有任何一方构造得出它的采集器 ⇒ 不会有东西进来')
  })

  it('shows the site icon the backend fetched, not a letter', async () => {
    getSources.mockResolvedValue(payload([
      row({ category: 'website', enabled: true, has_builder: true, icon: 'data:image/png;base64,AAA', source: 'zdbk', status: 'connected' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('source-avatar-img-zdbk').querySelector('img')?.getAttribute('src'))
      .toBe('data:image/png;base64,AAA'))
  })

  it('never prints a raw source id when the display name is missing', async () => {
    getSources.mockResolvedValue(payload([row({ enabled: true, has_builder: true, source: 'brand-new-thing', status: 'connected' })]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('Source with no recorded name')).toBeTruthy())
    expect(screen.queryByText('brand-new-thing')).toBeNull()
  })

  it('names a third-party source by the site it declared', async () => {
    // 用户 10-24：「网站就显示网站名就行了」。第三方包没进这张表，它那一行照样有名字——
    // 名字来自后端那一格读数（声明名或站点自报的名），不是界面替它起的类目词。
    getSources.mockResolvedValue(payload([
      row({ category: 'website', display_name: '本科教学管理信息服务平台', enabled: true, has_builder: true, source: 'zdbk', status: 'connected' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('本科教学管理信息服务平台')).toBeTruthy())
  })

  it('still lets the language catalogue name our own sources first', async () => {
    // 与 `setup_fields.label` 同一套路：声明给缺省值，四语目录优先。这一条钉的是**次序**——
    // 反过来就会把「微信」画成站方那句营销话。
    getSources.mockResolvedValue(payload([
      row({ display_name: '抓来的那个名', enabled: true, has_builder: true, source: 'zju', status: 'connected' })
    ]))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByText('Academic affairs (ZJU)')).toBeTruthy())
    expect(screen.queryByText('抓来的那个名')).toBeNull()
  })

  it('prints what the machine answered, verbatim, in the drawer', async () => {
    // 探针那句是**读数**（哪个目录、第几代加密），界面不转述（P6）；行上那一格仍然是
    // 「未连接」加目录里那句人话——红点留给真的故障。
    getSources.mockResolvedValue(payload([
      row({
        category: 'message',
        enabled: true,
        has_builder: false,
        probe: { code: 'client_not_logged_in', detail: '本地库在位：D:/x/dingtalk_encrypt.db（v3）', ready: false },
        source: 'dingtalk',
        status: 'disconnected',
        status_code: 'client_not_logged_in'
      })
    ]))

    render(<SourcesSettings />)
    await waitFor(() => expect(screen.getByText('Not connected')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /DingTalk/ }))

    await waitFor(() => expect(screen.getByTestId('source-probe-dingtalk').textContent)
      .toContain('本地库在位：D:/x/dingtalk_encrypt.db（v3）'))
    expect(screen.getByTestId('source-probe-dingtalk').textContent).toContain('Local prerequisite')
  })

  it('says "could not read" instead of drawing an empty list', async () => {
    getSources.mockRejectedValue(new Error('offline'))

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('sources-failed')).toBeTruthy())
  })

  it('degrades to that same row when the payload has no categories — it does NOT take the app down', async () => {
    // 装机现场真崩过一次：老后端回的 data 没有 `categories`，根 error boundary 把整个应用收走。
    getSources.mockResolvedValue({ ...payload([]), categories: undefined } as unknown as SourcesPayload)

    render(<SourcesSettings />)

    await waitFor(() => expect(screen.getByTestId('sources-failed')).toBeTruthy())
  })

  it('degrades to that same row when the payload has no sources either', async () => {
    getSources.mockResolvedValue({ checked_at: 'x', verdict: 'n/a' } as unknown as SourcesPayload)

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

describe('the drawer’s form', () => {
  const ICS = { help: 'Absolute path on this machine.', key: 'ics_path', kind: 'path' as const, label: 'Path to the .ics file', required: true }

  async function openDrawer(rowInput: SourceRow, displayName: string) {
    getSources.mockResolvedValue(payload([rowInput]))
    render(<SourcesSettings />)
    fireEvent.click(await screen.findByText(displayName))

    await waitFor(() => expect(screen.getByTestId(`source-drawer-${rowInput.source}`)).toBeTruthy())
  }

  it('holds the form only after the row is opened', async () => {
    await openDrawer(row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', status: 'disconnected' }), 'Timetable')

    expect(screen.getByTestId('setup-timetable')).toBeTruthy()
  })

  it('draws no form for a source that declares no fields', async () => {
    // 微信首次配置要的是扫码与密钥，两样都不在这一层 ⇒ 抽屉里就是没有那片表单，
    // 而不是给一片填不出东西的空表单。
    await openDrawer(row({ enabled: true, has_builder: true, source: 'dingtalk', status: 'disconnected' }), 'DingTalk')

    expect(screen.queryByTestId('setup-dingtalk')).toBeNull()
  })

  it('draws only the fields the source itself declared, and falls back to its label', async () => {
    await openDrawer(row({
      category: 'local',
      enabled: true,
      has_builder: true,
      setup_fields: [{ ...ICS, key: 'third_party_thing', label: 'Their own field' }],
      source: 'timetable',
      status: 'disconnected'
    }), 'Timetable')

    expect(screen.getByLabelText('Their own field')).toBeTruthy()
    expect(screen.queryByLabelText('Path to the .ics file')).toBeNull()
  })

  it('will not save while a required field is empty', async () => {
    await openDrawer(row({ category: 'local', enabled: true, has_builder: true, setup_fields: [ICS], source: 'timetable', status: 'disconnected' }), 'Timetable')

    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
    expect(saveSourceSetup).not.toHaveBeenCalled()
  })

  it('sends only the filled fields, never an empty string', async () => {
    saveSourceSetup.mockResolvedValue(undefined)
    await openDrawer(row({
      category: 'local',
      enabled: true,
      has_builder: true,
      setup_fields: [ICS, { help: '', key: 'note', kind: 'text', label: 'Note', required: false }],
      source: 'timetable',
      status: 'disconnected'
    }), 'Timetable')

    fireEvent.change(screen.getByLabelText('Path to the .ics file'), { target: { value: 'D:/x.ics' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(saveSourceSetup).toHaveBeenCalledWith('timetable', { ics_path: 'D:/x.ics' }))
  })

  it('masks a declared secret and never prefills it', async () => {
    // 用户 10-22：「密码什么的就不要明文显示。用星号」。
    // 旧版这条是反的（"不许有掩码框，因为声明层声明不出密钥"）——声明层现在能声明了，
    // 于是这一格钉的是两件事：**type=password**，且**这里永远没有它的旧值**
    // （后端只回"设过没有"，所以掩码框里不许出现一串星号假装是原值）。
    await openDrawer(row({
      category: 'local',
      enabled: true,
      has_builder: true,
      secrets: { password: true },
      setup: {},
      setup_fields: [{ help: '', key: 'password', kind: 'secret', label: 'Password', required: true }],
      source: 'timetable',
      status: 'connected'
    }), 'Timetable')

    const box = screen.getByLabelText('Password') as HTMLInputElement

    expect(box.type).toBe('password')
    expect(box.value).toBe('')
  })

  it('offers the cadence as controls, and stores the cron behind the words', async () => {
    // 用户 10-23：「频率设置这样用机器语言还是不好，可能可以用一个可交互的旋钮」⇒
    // 屏上是四颗能按的钮 + 滑杆/时间，cron 只在"自定义表达式"里；存进配置的仍是 cron。
    saveSourceSetup.mockResolvedValue(undefined)
    await openDrawer(row({
      category: 'local',
      enabled: true,
      has_builder: true,
      setup: { cadence: 'every 1h' },
      setup_fields: [
        { help: '', key: 'ics_path', kind: 'path', label: 'Path to the .ics file', required: true },
        { help: '', key: 'cadence', kind: 'cadence', label: 'Collection cadence', required: false }
      ],
      source: 'timetable',
      status: 'connected'
    }), 'Timetable')

    const field = () => screen.getByTestId('cadence-field')

    // 旧的两种形状都不在：没有下拉、也没有裸 cron 文本框（谁把它们改回来，这一条当场红）。
    expect(screen.queryByRole('combobox', { name: 'Collection cadence' })).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'Collection cadence' })).toBeNull()
    // 存量的 `every 1h` 读成"每隔几小时"那一档，滑杆停在 1。
    expect(screen.getByRole('button', { name: 'Every few hours' }).getAttribute('aria-pressed')).toBe('true')
    expect(field().getAttribute('data-cadence')).toBe('every 1h')
    // 拨到"每天"：出时间控件，存下去的形状立刻变成 `分 时 * * *`。
    fireEvent.click(screen.getByRole('button', { name: 'Daily' }))
    expect(field().querySelector('input[type="time"]')).toBeTruthy()
    expect(field().getAttribute('data-cadence')).toMatch(/^\d{2} \d{2} \* \* \*$/)
    // 拨到"每周"：多出七个星期钮，点周三之后 cron 的末位就是 3。
    fireEvent.click(screen.getByRole('button', { name: 'Weekly' }))
    fireEvent.click(screen.getByRole('button', { name: 'Wed' }))
    expect(field().getAttribute('data-cadence')).toMatch(/^\d{2} \d{2} \* \* 3$/)
    // 回到"每一轮"：存的是空串（＝跟着全局那一轮），不是把上一句留在文件里。
    fireEvent.click(screen.getByRole('button', { name: 'Every round' }))
    expect(field().getAttribute('data-cadence')).toBe('')
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