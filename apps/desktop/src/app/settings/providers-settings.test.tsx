import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const listOAuthProviders = vi.fn()
const disconnectOAuthProvider = vi.fn()
const getEnvVars = vi.fn()
const startManualProviderOAuth = vi.fn()
const onboarding = atom({ manual: false })

vi.mock('@/plobi', () => ({
  disconnectOAuthProvider: (providerId: string) => disconnectOAuthProvider(providerId),
  getEnvVars: () => getEnvVars(),
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

// The Desktop Quotas cards describe desktop software by its subscription, and the
// only thing that may say whether one is connected is the hub that serves it. The
// bug these pin down: the badge read a *click* instead, so a hub serving 18 models
// behind the user's back still showed "Not connected" until the button was pressed.
describe('Desktop Quotas cards report the hub verdict', () => {
  /** The frame the main process pushes when the hub vouches for one model. */
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

  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  async function renderDesktopQuotas() {
    const { ProvidersSettings } = await import('./providers-settings')

    return render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="desktop" />)
  }

  it('reports CodeBuddy connected without a single click once the hub serves it', async () => {
    frame('workbuddy', { models: ['hy3'] })

    await renderDesktopQuotas()

    // The button carries the state and nothing else does: no status chip beside
    // the name, no banner over the list. It reads connected and is disabled,
    // because there is nothing left to press.
    const connected = await screen.findByRole('button', { name: 'Connected' })

    expect((connected as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getAllByRole('button', { name: 'Connect' })).toHaveLength(1)
    expect(screen.getByText('CodeBuddy')).toBeTruthy()
    expect(screen.getByText(/Tencent CodeBuddy/)).toBeTruthy()
  })

  it('leaves both rows offering to connect while the hub has said nothing', async () => {
    await renderDesktopQuotas()

    const offers = await screen.findAllByRole('button', { name: 'Connect' })

    expect(offers).toHaveLength(2)

    for (const offer of offers) {
      expect((offer as HTMLButtonElement).disabled).toBe(false)
    }
  })

  it('keeps the hub\'s reason on the button that would fix it, not on the row', async () => {
    frame('workbuddy', { blocked: { 'hy3': 'credential_expired' }, models: [] })

    await renderDesktopQuotas()

    const offers = await screen.findAllByRole('button', { name: 'Connect' })

    expect(offers).toHaveLength(2)
    // The software is unusable, so the button stays pressable and carries the
    // reason as its tooltip — and the reason is nowhere else on the page.
    const unusable = offers.find(offer => /its sign-in has expired/.test(offer.getAttribute('title') ?? ''))

    expect(unusable).toBeTruthy()
    expect(screen.queryByText(/sign-in has expired/)).toBeNull()
  })

  it('never reports a connected software the hub answered nothing about', async () => {
    applyDiscoveredQuotaApps([
      { answered: false, apiKey: 'sk-local-dev-key', baseUrl: 'http://127.0.0.1:8000/v1', blocked: {}, id: 'workbuddy', models: [] }
    ])

    await renderDesktopQuotas()

    expect(await screen.findAllByRole('button', { name: 'Connect' })).toHaveLength(2)
  })
})

// Connect once and the hub keeps the app wired up on every launch from then on —
// that is the whole point of a subscription leg. Disconnect is the user taking
// that back: Plobi stops routing through the software, and it has to *stay*
// stopped, because the hub re-reads every 60 seconds and will happily report the
// app as serving the whole time.
describe('Desktop Quotas disconnect', () => {
  function frame(id: string, models: string[]) {
    applyDiscoveredQuotaApps([
      { answered: true, apiKey: 'sk-local-dev-key', baseUrl: 'http://127.0.0.1:8000/v1', blocked: {}, id, models }
    ])
  }

  beforeEach(() => {
    $connectedDesktopApps.set({})
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    // The Electron bridge a jsdom window does not have. `auth` is the shell's
    // "re-read the hub" call, so a manual Connect round-trips through it.
    window.plobiDesktop = {
      plobiGateway: {
        auth: vi.fn().mockResolvedValue({
          apiKey: 'sk-local-dev-key',
          baseUrl: 'http://127.0.0.1:8000/v1',
          models: ['hy3'],
          ok: true
        })
      },
      requestQuotaApps: vi.fn()
    } as never
  })

  async function renderDesktopQuotas() {
    const { ProvidersSettings } = await import('./providers-settings')

    return render(<ProvidersSettings onClose={vi.fn()} onViewChange={vi.fn()} view="desktop" />)
  }

  /** The cards render in catalog order: Antigravity, then CodeBuddy. */
  const codeBuddyConnect = () => screen.getAllByRole('button', { name: 'Connect' })[1]

  it('offers Disconnect only once something is connected', async () => {
    await renderDesktopQuotas()

    expect((await screen.findAllByRole('button', { name: 'Connect' })).length).toBe(2)
    expect(screen.queryByRole('button', { name: 'Disconnect' })).toBeNull()

    frame('workbuddy', ['hy3'])
    fireEvent.click(codeBuddyConnect())

    expect(await screen.findByRole('button', { name: 'Disconnect' })).toBeTruthy()
  })

  it('stops routing through the software once the user disconnects it', async () => {
    frame('workbuddy', ['hy3'])

    await renderDesktopQuotas()
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }))

    // The hub's next frame still reports the models. The flag is what decides,
    // and that is the whole reason it is a flag and not a deleted record.
    frame('workbuddy', ['hy3'])

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy', 'workbuddy_api'])).toEqual({ state: 'off' })
    expect(hubAppOfModel('workbuddy/hy3')).toBeNull()
    expect(quotaSelectionBlock('workbuddy/hy3')).toBe('disconnected')
  })

  it('asks first, and says which of the two switches this is', async () => {
    frame('workbuddy', ['hy3'])

    await renderDesktopQuotas()
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }))

    const asked = vi.mocked(window.confirm).mock.calls[0]?.[0] ?? ''

    expect(asked).toContain('CodeBuddy')
    // Signing out of the app is a different button, somewhere else. This copy has
    // to draw the line itself, or the user reads "Disconnect" as "log me out".
    expect(asked).toContain('does not sign you out')
  })

  it('keeps the connection when the user declines the confirmation', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    frame('workbuddy', ['hy3'])

    await renderDesktopQuotas()
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }))

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy', 'workbuddy_api'])).toEqual({
      models: ['hy3'],
      state: 'ok'
    })
  })

  it('brings the software back when the user connects again', async () => {
    frame('workbuddy', ['hy3'])

    await renderDesktopQuotas()
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }))
    fireEvent.click(await codeBuddyConnect())

    await waitFor(() =>
      expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy', 'workbuddy_api'])).toEqual({
        models: ['hy3'],
        state: 'ok'
      })
    )
    expect(quotaSelectionBlock('workbuddy/hy3')).toBeNull()
  })
})
