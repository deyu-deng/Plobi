import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $connectedDesktopApps,
  applyDiscoveredQuotaApps,
  hubAppOfModel,
  quotaAppVerdict,
  quotaSelectionBlock
} from '@/store/desktop-quotas'
import type { EnvVarInfo, OAuthProvider } from '@/types/plobi'

import type { AuthCatalogRow } from './quota-login-cards'

const listOAuthProviders = vi.fn()
const disconnectOAuthProvider = vi.fn()
const getEnvVars = vi.fn()
const startManualProviderOAuth = vi.fn()
const onboarding = atom({ manual: false })

vi.mock('@/plobi', () => ({
  disconnectOAuthProvider: (providerId: string) => disconnectOAuthProvider(providerId),
  getEnvVars: () => getEnvVars(),
  // providers-settings 在挂载时会问一次「aigw 这一行让不让选」（readHubSelectable），
  // 以前这个 mock 只给了三个键，于是每条用例都先抛一个 No "getGlobalModelOptions" export。
  getGlobalModelOptions: () => Promise.resolve({ providers: [] as Array<{ selectable?: boolean; slug: string }> }),
  listOAuthProviders: () => listOAuthProviders()
}))

vi.mock('@/store/onboarding', () => ({
  $desktopOnboarding: onboarding,
  startManualProviderOAuth: (providerId: string) => startManualProviderOAuth(providerId)
}))

function provider(id: string, loggedIn: boolean, patch: Partial<OAuthProvider> = {}): OAuthProvider {
  return {
    cli_command: `plobi auth add ${id}`,
    disconnectable: true,
    docs_url: '',
    flow: 'device_code',
    id,
    name: id === 'nous' ? 'Nous Portal' : 'MiniMax',
    status: {
      logged_in: loggedIn
    },
    ...patch
  }
}

// One `/api/env` row (an EnvVarInfo) for the API-keys view. Mirrors the
// `provider()` factory above: a valid base + per-test overrides, typed against
// the real response shape so it can't drift from EnvVarInfo.
function keyVar(patch: Partial<EnvVarInfo> = {}): EnvVarInfo {
  return {
    advanced: false,
    category: 'provider',
    description: '',
    is_password: true,
    is_set: false,
    provider: '',
    provider_label: '',
    redacted_value: null,
    tools: [],
    url: '',
    ...patch
  }
}

beforeEach(() => {
  onboarding.set({ manual: false })
  getEnvVars.mockResolvedValue({})
  disconnectOAuthProvider.mockResolvedValue({ ok: true, provider: 'nous' })
  listOAuthProviders.mockResolvedValue({
    providers: [provider('nous', true), provider('minimax-oauth', false)]
  })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

async function renderProvidersSettings() {
  const { ProvidersSettings } = await import('./providers-settings')

  return render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="accounts" />)
}

describe('ProvidersSettings', () => {
  it('disconnects a connected provider account and refreshes the accounts list', async () => {
    await renderProvidersSettings()

    const remove = await screen.findByRole('button', { name: 'Remove Nous Portal' })
    fireEvent.click(remove)

    await waitFor(() => expect(disconnectOAuthProvider).toHaveBeenCalledWith('nous'))
    expect(listOAuthProviders).toHaveBeenCalledTimes(2)
  })

  it('keeps provider selection separate from account removal', async () => {
    await renderProvidersSettings()

    fireEvent.click(await screen.findByText('Nous Portal'))

    expect(startManualProviderOAuth).toHaveBeenCalledWith('nous')
    expect(disconnectOAuthProvider).not.toHaveBeenCalled()
  })

  it('does not offer removal for externally managed providers', async () => {
    listOAuthProviders.mockResolvedValue({
      providers: [
        provider('qwen-oauth', true, {
          cli_command: 'plobi auth add qwen-oauth',
          disconnect_hint: "Use `plobi auth add qwen-oauth` or that provider's CLI to remove it.",
          disconnectable: false,
          flow: 'external',
          name: 'Qwen (via Qwen CLI)'
        })
      ]
    })

    await renderProvidersSettings()

    expect(await screen.findByText('Qwen Code')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Remove Qwen Code' })).toBeNull()
    expect(screen.getByText(/managed by its own CLI/)).toBeTruthy()
  })

  it('renders a Keys card for a backend-tagged provider with no PROVIDER_GROUPS prefix', async () => {
    // A provider the backend catalog tags (provider/provider_label) but that has
    // no desktop PROVIDER_GROUPS prefix row must still render its own card —
    // this is the GUI/CLI drift fix: membership comes from the backend, not
    // from the hand-maintained prefix list.
    getEnvVars.mockResolvedValue({
      WIDGETAI_API_KEY: keyVar({
        provider: 'widgetai',
        provider_label: 'WidgetAI',
        url: 'https://widgetai.example/keys'
      })
    })
    listOAuthProviders.mockResolvedValue({ providers: [] })

    const { ProvidersSettings } = await import('./providers-settings')
    render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="keys" />)

    expect(await screen.findByText('WidgetAI')).toBeTruthy()
  })

  it('orders API-key providers by priority then name, and filters them via search', async () => {
    // These three providers have no curated PROVIDER_GROUPS priority, so they
    // share the default priority and fall back to alphabetical among themselves
    // (Acme, Middle, Zebra) — exercising the name tiebreak of the priority sort.
    getEnvVars.mockResolvedValue({
      ZEBRA_API_KEY: keyVar({ provider: 'zebra', provider_label: 'Zebra' }),
      ACME_API_KEY: keyVar({ provider: 'acme', provider_label: 'Acme' }),
      MIDDLE_API_KEY: keyVar({ provider: 'middle', provider_label: 'Middle' })
    })
    listOAuthProviders.mockResolvedValue({ providers: [] })

    const { ProvidersSettings } = await import('./providers-settings')
    render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="keys" />)

    // Equal priority → alphabetical tiebreak: Acme, Middle, Zebra.
    await screen.findByText('Acme')
    const labels = screen.getAllByText(/Acme|Middle|Zebra/).map(el => el.textContent)
    expect(labels).toEqual(['Acme', 'Middle', 'Zebra'])

    // Typing narrows the list to matching providers only.
    const search = screen.getByPlaceholderText('Search providers…')
    fireEvent.change(search, { target: { value: 'mid' } })

    await waitFor(() => expect(screen.queryByText('Acme')).toBeNull())
    expect(screen.getByText('Middle')).toBeTruthy()
    expect(screen.queryByText('Zebra')).toBeNull()

    // A non-matching query shows the empty-state copy.
    fireEvent.change(search, { target: { value: 'nonesuch-xyz' } })
    expect(await screen.findByText('No providers match your search.')).toBeTruthy()
  })
})

// The card grammar (`Docs/specs/settings-card-system.md`): one row per piece of
// software the hub says is on this machine, and the *shape* of the single control on
// that row is its state. The bugs these pin down are the two ways this screen used to
// lie — a hand-copied list of apps it showed while the hub had said nothing, and two
// text buttons that spelled the state out as sentences.
const authRow = (id: string, over: Partial<AuthCatalogRow> = {}): AuthCatalogRow => ({
  aggregate: true,
  id,
  installed: true,
  kind: 'oauth',
  live: false,
  name:
    id === 'workbuddy'
      ? 'CodeBuddy'
      : id === 'qwen-cli'
        ? 'Qwen Code'
        : id === 'gemini_api'
          ? 'Gemini CLI'
          : 'Antigravity',
  reason: 'not signed in',
  ...over
})

describe('Subscriptions cards are the hub catalog', () => {
  /** The frame the main process pushes when the hub vouches for a model. */
  function frame(id: string, over: { blocked?: Record<string, string>; models?: string[] } = {}) {
    applyDiscoveredQuotaApps([
      {
        answered: true,
        apiKey: 'sk-local-dev-key',
        baseUrl: 'http://127.0.0.1:8000/v1',
        blocked: over.blocked ?? {},
        id,
        models: over.models ?? []
      }
    ])
  }

  function shell(rows: AuthCatalogRow[], auth = vi.fn().mockResolvedValue({ models: [], ok: true })) {
    window.plobiDesktop = {
      authCatalog: vi.fn().mockResolvedValue(rows),
      plobiGateway: { auth },
      requestQuotaApps: vi.fn()
    } as never

    return auth
  }

  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  async function renderSubscriptions() {
    const { ProvidersSettings } = await import('./providers-settings')

    return render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="desktop" />)
  }

  it('shows one row per software the hub reported, each with exactly one control', async () => {
    shell([
      authRow('antigravity'),
      authRow('workbuddy', { kind: 'local_session', live: true, reason: 'signed in' })
    ])

    await renderSubscriptions()

    expect(await screen.findByText('CodeBuddy')).toBeTruthy()
    expect(screen.getByText('Antigravity')).toBeTruthy()
    // The invariant, not a snapshot: a row never carries two controls, and a software
    // the hub did not list never appears at all.
    expect(screen.getAllByRole('switch')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Set up' })).toHaveLength(1)
  })

  it('offers `+` for a signed-out software and a switch for one already in use', async () => {
    shell([authRow('antigravity'), authRow('workbuddy', { kind: 'local_session', live: true, reason: 'signed in' })])
    frame('workbuddy', { models: ['hy3'] })

    await renderSubscriptions()

    const plus = await screen.findByRole('button', { name: 'Set up' })

    expect((plus as HTMLButtonElement).disabled).toBe(false)
    // Shape is the state: the switch says "in use" without the row saying it in words.
    expect(screen.getAllByRole('switch')[0]).toBeTruthy()
    expect(screen.queryByText('Connected')).toBeNull()
    expect(screen.queryByText('Not connected')).toBeNull()
  })

  it('keeps asking while the browser still has the sign-in', async () => {
    // 登录跳浏览器那一步是分钟级的。以前这个视图只在挂载和点击回执时读一次目录，
    // 所以他登录完回到屏幕上看到的还是那个 `+`（2026-10-28 现场报）。
    shell([authRow('gemini_api', { login: { state: 'pending', url: 'https://accounts.google.com/o/oauth2/v2/auth?x=1' } })] as AuthCatalogRow[])
    vi.useFakeTimers()

    try {
      await act(async () => {
        const { ProvidersSettings } = await import('./providers-settings')

        render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="desktop" />)
        await Promise.resolve()
      })

      const reads = window.plobiDesktop.authCatalog as ReturnType<typeof vi.fn>
      const before = reads.mock.calls.length

      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000)
      })

      expect(reads.mock.calls.length).toBeGreaterThan(before)
    } finally {
      vi.useRealTimers()
    }
  })

  it('also re-reads when nothing is pending, because a spawn leg reports later', async () => {
    // Measured 2026-10-29: cline answers the hub's probe ~10 s after boot, qwen ~25 s.
    // A Settings view opened inside that window used to keep the `+` it read first,
    // until he closed and reopened it — the same complaint, one boot later.
    shell([authRow('cline_cli', { signed_in: false, live: false, reason: 'not_signed_in' })])
    vi.useFakeTimers()

    try {
      await act(async () => {
        const { ProvidersSettings } = await import('./providers-settings')

        render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="desktop" />)
        await Promise.resolve()
      })

      const reads = window.plobiDesktop.authCatalog as ReturnType<typeof vi.fn>
      const before = reads.mock.calls.length

      await act(async () => {
        await vi.advanceTimersByTimeAsync(16000)
      })

      expect(reads.mock.calls.length).toBeGreaterThan(before)
    } finally {
      vi.useRealTimers()
    }
  })

  it('gives a signed-in login-only CLI no control, because Plobi has nothing to toggle', async () => {
    shell([authRow('qwen-cli', { aggregate: false, kind: 'cli_login', live: true, reason: 'signed in' })])

    await renderSubscriptions()

    expect(await screen.findByText('Qwen Code')).toBeTruthy()
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    expect(screen.queryAllByRole('button', { name: 'Set up' })).toHaveLength(0)
  })

  it('gives the Gemini leg a plus, because signing it in is a click and not a terminal', async () => {
    // The shape this whole file exists to keep: `gemini_api` is a hub leg whose sign-in
    // Plobi can start. It used to be a login-only row whose button opened a video.
    shell([authRow('gemini_api', { kind: 'oauth', live: false })])

    await renderSubscriptions()

    expect(await screen.findByText('Gemini CLI')).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Set up' })).toBeTruthy()
  })

  it('shows no software names while the shell has not answered', async () => {
    // A catalog read that never returns: the old screen fell back to a checked-in list
    // of two apps here, which is exactly how an unconfigured machine saw model rows.
    window.plobiDesktop = {
      authCatalog: vi.fn().mockReturnValue(new Promise(() => undefined)),
      plobiGateway: { auth: vi.fn() },
      requestQuotaApps: vi.fn()
    } as never

    await renderSubscriptions()

    expect(screen.queryByText('CodeBuddy')).toBeNull()
    expect(screen.queryByText('Antigravity')).toBeNull()
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    expect(screen.queryAllByRole('button', { name: 'Set up' })).toHaveLength(0)
  })

  it('keeps the hub\'s reason on the control that would fix it, not on the row', async () => {
    shell([authRow('workbuddy', { kind: 'local_session', live: true, reason: 'signed in' })])
    frame('workbuddy', { blocked: { 'hy3': 'credential_expired' }, models: [] })

    await renderSubscriptions()

    const control = await screen.findByRole('switch')

    // 11-04 之后这条理由不再挂在 switch 的 tooltip 上（那里只留点击失败），
    // 而是卡片下方那一行 body copy（`below`，role="note"）。switch 仍然可以按，
    // 因为按下去是"把这个腿关掉"，正是它该做的事。
    expect(control.getAttribute('title')).toBeNull()
    expect((await screen.findByRole('note')).textContent).toMatch(/sign-in has expired/)
  })

  it('names the refusal the same way it names an expired sign-in: on the control only', async () => {
    // The hub measures which ids the upstream actually serves (`model_rejected`); the
    // row must not turn that measurement into a sentence about the model.
    shell([authRow('workbuddy', { kind: 'local_session', live: true, reason: 'signed in' })])
    frame('workbuddy', { blocked: { 'hy3': 'model_rejected' }, models: [] })

    await renderSubscriptions()

    const control = await screen.findByRole('switch')

    // 与上一条同样的落点：文案在卡片下方的 note 里，switch 上没有 tooltip，
    // 而且理由码本身（`model_rejected`）不该作为句子漏到屏幕上。
    expect(control.getAttribute('title')).toBeNull()
    expect((await screen.findByRole('note')).textContent).toMatch(/did not serve that model/)
    expect(screen.queryByText('model_rejected')).toBeNull()
  })
})

// Turning the switch off is the user taking back the routing decision, and it has to
// *stay* taken back: the hub re-reads every 60 seconds and will happily report the
// app as serving the whole time. So it is a flag on the store record, not a deletion.
describe('Subscriptions switch', () => {
  function frame(id: string, models: string[]) {
    applyDiscoveredQuotaApps([
      { answered: true, apiKey: 'sk-local-dev-key', baseUrl: 'http://127.0.0.1:8000/v1', blocked: {}, id, models }
    ])
  }

  const auth = vi.fn()

  beforeEach(() => {
    $connectedDesktopApps.set({})
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    auth.mockResolvedValue({ apiKey: 'sk-local-dev-key', baseUrl: 'http://127.0.0.1:8000/v1', models: ['hy3'], ok: true })
    // The Electron bridge a jsdom window does not have.
    window.plobiDesktop = {
      authCatalog: vi.fn().mockResolvedValue([
        authRow('antigravity'),
        authRow('workbuddy', { kind: 'local_session', live: true, reason: 'signed in' })
      ]),
      plobiGateway: { auth },
      requestQuotaApps: vi.fn()
    } as never
  })

  async function renderSubscriptions() {
    const { ProvidersSettings } = await import('./providers-settings')

    return render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="desktop" />)
  }

  /** The cards render in catalog order: Antigravity's `+`, then CodeBuddy's switch. */
  function codeBuddySwitch() {
    return screen.getAllByRole('switch')[0] as HTMLElement
  }

  it('changes the shape when the hub starts serving it', async () => {
    // First read: signed out, so a `+`. The `+` now opens first-run setup instead of
    // calling the shell itself; the sign-in inside it is what makes the hub answer
    // "signed in", and the very same row then shows a switch — the state moved into
    // the shape.
    const catalog = vi
      .fn()
      .mockResolvedValueOnce([authRow('antigravity')])
      .mockResolvedValueOnce([authRow('antigravity', { live: true, reason: 'signed in' })])

    window.plobiDesktop = {
      authCatalog: catalog,
      plobiGateway: { auth },
      requestQuotaApps: vi.fn()
    } as never

    await renderSubscriptions()
    fireEvent.click(await screen.findByRole('button', { name: 'Set up' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(1))
  })

  it('stops routing through the software when the user turns it off', async () => {
    frame('workbuddy', ['hy3'])

    await renderSubscriptions()
    fireEvent.click(await screen.findByRole('switch'))

    // The hub's next frame still reports the models. The flag is what decides,
    // and that is the whole reason it is a flag and not a deleted record.
    frame('workbuddy', ['hy3'])

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy', 'workbuddy_api'])).toEqual({ state: 'off' })
    expect(hubAppOfModel('workbuddy/hy3')).toBeNull()
    expect(quotaSelectionBlock('workbuddy/hy3')).toBe('disconnected')
  })

  it('asks first, and says which of the two switches this is', async () => {
    frame('workbuddy', ['hy3'])

    await renderSubscriptions()
    fireEvent.click(await screen.findByRole('switch'))

    const asked = vi.mocked(window.confirm).mock.calls[0]?.[0] ?? ''

    expect(asked).toContain('CodeBuddy')
    // Signing out of the app is a different button, somewhere else. This copy has
    // to draw the line itself, or the user reads the switch as "log me out".
    expect(asked).toContain('does not sign you out')
  })

  it('stays on when the user declines the confirmation', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    frame('workbuddy', ['hy3'])

    await renderSubscriptions()
    fireEvent.click(await screen.findByRole('switch'))

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy', 'workbuddy_api'])).toEqual({
      models: ['hy3'],
      state: 'ok'
    })
    expect((codeBuddySwitch() as HTMLButtonElement).getAttribute('aria-checked')).toBe('true')
  })

  it('brings the software back on the switch, with no dialog', async () => {
    frame('workbuddy', ['hy3'])

    await renderSubscriptions()
    fireEvent.click(await screen.findByRole('switch'))
    vi.mocked(window.confirm).mockClear()

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy'])).toEqual({ state: 'off' })

    fireEvent.click(codeBuddySwitch())

    // 打开是"请求"，不是判决：记录先落在 answered:false，等 hub 下一帧把真实模型
    // 带回来才算 ok（user 2026-11-04）。所以这里必须补一帧，不能只等 UI。
    frame('workbuddy', ['hy3'])

    await waitFor(() =>
      expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy', 'workbuddy_api'])).toEqual({
        models: ['hy3'],
        state: 'ok'
      })
    )
    expect(quotaSelectionBlock('workbuddy/hy3')).toBeNull()
    expect(window.confirm).not.toHaveBeenCalled()
  })

  it('starts the sign-in through the shell and waits in the setup dialog', async () => {
    let release: (value: { models: string[]; ok: boolean }) => void = () => undefined

    auth.mockImplementationOnce(
      () =>
        new Promise(resolve => {
          release = resolve
        })
    )

    await renderSubscriptions()
    fireEvent.click(await screen.findByRole('button', { name: 'Set up' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(auth).toHaveBeenCalledWith('antigravity'))
    // The browser half happens outside this window, so the dialog stops offering the
    // button and says it is waiting, instead of inviting a second press.
    expect(await screen.findByText(/Waiting for sign-in/)).toBeTruthy()

    release({ models: [], ok: true })

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull())
  })
})

