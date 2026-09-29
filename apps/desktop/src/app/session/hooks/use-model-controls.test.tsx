import { QueryClient } from '@tanstack/react-query'
import { cleanup, render, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getGlobalModelInfo } from '@/plobi'
import { agentScope, L1_SCOPE } from '@/app/console/chat/scope'
import {
  $activeSessionId,
  $currentModel,
  $currentProvider,
  setComposerModelScope,
  setCurrentModel,
  setCurrentProvider
} from '@/store/session'

import { useModelControls } from './use-model-controls'

const setGlobalModel = vi.fn()
const notifyError = vi.fn()
const quotaProvider = vi.fn((_slug: string): boolean => false)

vi.mock('@/plobi', () => ({
  getGlobalModelInfo: vi.fn(),
  setGlobalModel: (...args: Parameters<typeof setGlobalModel>) => setGlobalModel(...args)
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      desktop: {
        modelSwitchFailed: 'Model switch failed'
      }
    }
  })
}))

vi.mock('@/store/notifications', () => ({
  notifyError: (...args: Parameters<typeof notifyError>) => notifyError(...args)
}))

// The desktop-quota catalog belongs to `store/desktop-quotas`; this hook only
// asks "is this a quota app?". Mock the predicate so the branch pinned below is
// the real early-return branch without dragging the quota store in.
vi.mock('@/store/desktop-quotas', () => ({
  isDesktopQuotaProvider: (slug: string) => quotaProvider(slug)
}))

type Controls = ReturnType<typeof useModelControls>

type ScopeProp = Parameters<typeof useModelControls>[0]['scope']

function Harness({
  activeSessionId,
  isAgentSession,
  onReady,
  requestGateway,
  scope
}: {
  activeSessionId: string | null
  isAgentSession?: boolean
  onReady: (controls: Controls) => void
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  scope?: ScopeProp
}) {
  const controls = useModelControls({
    activeSessionId,
    isAgentSession,
    scope,
    queryClient: new QueryClient(),
    requestGateway
  })

  onReady(controls)

  return null
}

describe('useModelControls', () => {
  beforeEach(() => {
    window.localStorage.clear()
    quotaProvider.mockReset().mockReturnValue(false)
    // Back to the flat (secretary) cell so no test inherits another scope's box.
    setComposerModelScope(null)
    $activeSessionId.set(null)
    setCurrentModel('')
    setCurrentProvider('')
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    setComposerModelScope(null)
    $activeSessionId.set(null)
    setCurrentModel('')
    setCurrentProvider('')
    window.localStorage.clear()
  })

  it('applies the global model when there is no active runtime session', async () => {
    vi.mocked(getGlobalModelInfo).mockResolvedValue({
      model: 'openai/gpt-5.5',
      provider: 'openai-codex'
    })

    const { result } = renderHook(() =>
      useModelControls({
        activeSessionId: null,
        queryClient: new QueryClient(),
        requestGateway: vi.fn()
      })
    )

    await result.current.refreshCurrentModel()

    expect($currentModel.get()).toBe('openai/gpt-5.5')
    expect($currentProvider.get()).toBe('openai-codex')
  })

  it('does not clobber the active session footer state with global model info', async () => {
    setCurrentModel('deepseek/deepseek-v4-pro')
    setCurrentProvider('deepseek')
    $activeSessionId.set('runtime-1')
    vi.mocked(getGlobalModelInfo).mockResolvedValue({
      model: 'openai/gpt-5.5',
      provider: 'openai-codex'
    })

    const { result } = renderHook(() =>
      useModelControls({
        activeSessionId: 'runtime-1',
        queryClient: new QueryClient(),
        requestGateway: vi.fn()
      })
    )

    await result.current.refreshCurrentModel()

    expect($currentModel.get()).toBe('deepseek/deepseek-v4-pro')
    expect($currentProvider.get()).toBe('deepseek')
  })

  it('routes active-session picker changes through config.set with an explicit session-scoped provider', async () => {
    const requestGateway = vi.fn(async () => ({ key: 'model', value: 'claude-sonnet-4.6' }) as never)
    let controls!: Controls

    render(
      <Harness activeSessionId="session-1" onReady={value => (controls = value)} requestGateway={requestGateway} />
    )

    await expect(
      controls.selectModel({
        model: 'claude-sonnet-4.6',
        provider: 'anthropic'
      })
    ).resolves.toBe(true)

    expect(requestGateway).toHaveBeenCalledWith('config.set', {
      session_id: 'session-1',
      key: 'model',
      value: 'claude-sonnet-4.6 --provider anthropic --session'
    })
    expect(requestGateway).not.toHaveBeenCalledWith('slash.exec', expect.anything())
  })

  // 裁定 43: an L2 分身 is its own profile and owns its own model default. The
  // base already persists by default (`model.persist_switch_by_default`), so the
  // only thing an agent conversation must drop is the explicit `--session` opt-out.
  it('persists the pick for an L2 conversation instead of session-scoping it', async () => {
    const requestGateway = vi.fn(async () => ({ key: 'model', value: 'kimi-k2' }) as never)
    let controls!: Controls

    render(
      <Harness
        activeSessionId="session-1"
        isAgentSession
        onReady={value => (controls = value)}
        requestGateway={requestGateway}
      />
    )

    await expect(
      controls.selectModel({
        model: 'kimi-k2',
        provider: 'moonshot'
      })
    ).resolves.toBe(true)

    expect(requestGateway).toHaveBeenCalledWith('config.set', {
      session_id: 'session-1',
      key: 'model',
      value: 'kimi-k2 --provider moonshot'
    })
  })

  it('session-scopes MoA preset selections so they cannot persist as the global gateway default', async () => {
    const requestGateway = vi.fn(async () => ({ key: 'model', value: 'BeastMode' }) as never)
    let controls!: Controls

    render(
      <Harness activeSessionId="session-1" onReady={value => (controls = value)} requestGateway={requestGateway} />
    )

    await expect(
      controls.selectModel({
        model: 'BeastMode',
        provider: 'moa'
      })
    ).resolves.toBe(true)

    expect(requestGateway).toHaveBeenCalledWith('config.set', {
      session_id: 'session-1',
      key: 'model',
      value: 'BeastMode --provider moa --session'
    })
  })

  it('stores a no-session pick as UI state with no gateway or global write', async () => {
    const requestGateway = vi.fn()
    let controls!: Controls

    render(<Harness activeSessionId={null} onReady={value => (controls = value)} requestGateway={requestGateway} />)

    await expect(
      controls.selectModel({
        model: 'claude-sonnet-4.6',
        provider: 'anthropic'
      })
    ).resolves.toBe(true)

    // The pick is plain UI state; session.create ships it later. Nothing touches
    // the gateway or the profile default here.
    expect($currentModel.get()).toBe('claude-sonnet-4.6')
    expect($currentProvider.get()).toBe('anthropic')
    expect(requestGateway).not.toHaveBeenCalled()
    expect(setGlobalModel).not.toHaveBeenCalled()
  })

  it('seeds an empty composer model from global but never clobbers a pick', async () => {
    vi.mocked(getGlobalModelInfo).mockResolvedValue({ model: 'openai/gpt-5.5', provider: 'openai-codex' })

    const { result } = renderHook(() =>
      useModelControls({
        activeSessionId: null,
        queryClient: new QueryClient(),
        requestGateway: vi.fn()
      })
    )

    // Empty → seeds the default.
    await result.current.refreshCurrentModel()
    expect($currentModel.get()).toBe('openai/gpt-5.5')

    // A user pick must survive the lifecycle refreshes that fire on boot / fresh
    // draft / session events.
    setCurrentModel('anthropic/claude-sonnet-4.6')
    setCurrentProvider('anthropic')
    await result.current.refreshCurrentModel()
    expect($currentModel.get()).toBe('anthropic/claude-sonnet-4.6')

    // A profile swap forces a reseed to the new profile's default.
    await result.current.refreshCurrentModel(true)
    expect($currentModel.get()).toBe('openai/gpt-5.5')
  })

  // ── 裁定 44:「一个 Agent 一张嘴 / 习惯是人的属性，不是对话窗口的属性」────────
  // The composer's model + provider persist per chat scope (`agentScope`), while
  // the L1 secretary and every chat outside a project keep the ORIGINAL flat
  // keys — no migration, no rename, no per-session-id cell.
  const agentCells = () =>
    Object.keys(window.localStorage).filter(key => /^plobi\.desktop\.composer\.(model|provider)\./.test(key))

  it('stores an agent pick in that agent\'s own cell and leaves every other cell alone', async () => {
    window.localStorage.setItem('plobi.desktop.composer.model', 'openai/gpt-5.5')
    window.localStorage.setItem('plobi.desktop.composer.provider', 'openai-codex')
    window.localStorage.setItem('plobi.desktop.composer.model.agent:prism', 'kimi-k2')
    window.localStorage.setItem('plobi.desktop.composer.provider.agent:prism', 'moonshot')

    const requestGateway = vi.fn()
    let controls!: Controls

    render(
      <Harness
        activeSessionId={null}
        scope={agentScope('aura')}
        onReady={value => (controls = value)}
        requestGateway={requestGateway}
      />
    )

    await expect(
      controls.selectModel({ model: 'gemini-3-pro', provider: 'google' })
    ).resolves.toBe(true)

    expect(window.localStorage.getItem('plobi.desktop.composer.model.agent:aura')).toBe('gemini-3-pro')
    expect(window.localStorage.getItem('plobi.desktop.composer.provider.agent:aura')).toBe('google')
    // Prism keeps its own pick — the whole point of the report.
    expect(window.localStorage.getItem('plobi.desktop.composer.model.agent:prism')).toBe('kimi-k2')
    // The secretary's flat cell is neither written nor moved.
    expect(window.localStorage.getItem('plobi.desktop.composer.model')).toBe('openai/gpt-5.5')
    expect(window.localStorage.getItem('plobi.desktop.composer.provider')).toBe('openai-codex')
    expect(requestGateway).not.toHaveBeenCalled()
  })

  it('reads and writes the original flat keys for the L1 secretary', async () => {
    window.localStorage.setItem('plobi.desktop.composer.model', 'deepseek/deepseek-v4-pro')
    window.localStorage.setItem('plobi.desktop.composer.provider', 'deepseek')

    let controls!: Controls

    render(<Harness activeSessionId={null} scope={L1_SCOPE} onReady={value => (controls = value)} requestGateway={vi.fn()} />)

    await expect(
      controls.selectModel({ model: 'anthropic/claude-sonnet-4.6', provider: 'anthropic' })
    ).resolves.toBe(true)

    expect(window.localStorage.getItem('plobi.desktop.composer.model')).toBe('anthropic/claude-sonnet-4.6')
    expect(window.localStorage.getItem('plobi.desktop.composer.provider')).toBe('anthropic')
    // A chat with no agent in front of you never grows an agent-scoped cell.
    expect(agentCells()).toEqual([])
  })

  it('never lets the flat cell outrank a forced profile reseed', async () => {
    // Regression guard for the "forgets the LLM setting" profile-swap fix: the
    // secretary scope resolves to the flat keys, and the flat keys are NOT
    // authoritative over `force` — the new profile's default is.
    window.localStorage.setItem('plobi.desktop.composer.model', 'deepseek/deepseek-v4-pro')
    vi.mocked(getGlobalModelInfo).mockResolvedValue({ model: 'openai/gpt-5.5', provider: 'openai-codex' })

    const { result } = renderHook(() =>
      useModelControls({
        activeSessionId: null,
        scope: L1_SCOPE,
        queryClient: new QueryClient(),
        requestGateway: vi.fn()
      })
    )

    await result.current.refreshCurrentModel(true)

    expect($currentModel.get()).toBe('openai/gpt-5.5')
    expect(window.localStorage.getItem('plobi.desktop.composer.model')).toBe('openai/gpt-5.5')
  })

  it('re-seeds the pill from the other agent\'s own cell on an agent switch', async () => {
    window.localStorage.setItem('plobi.desktop.composer.model.agent:prism', 'kimi-k2')
    window.localStorage.setItem('plobi.desktop.composer.provider.agent:prism', 'moonshot')
    vi.mocked(getGlobalModelInfo).mockResolvedValue({ model: 'openai/gpt-5.5', provider: 'openai-codex' })

    const { result, rerender } = renderHook(
      ({ scope }: { scope: ScopeProp }) =>
        useModelControls({
          activeSessionId: null,
          scope,
          queryClient: new QueryClient(),
          requestGateway: vi.fn()
        }),
      { initialProps: { scope: agentScope('aura') as ScopeProp } }
    )

    // Aura has no cell of its own yet → the profile default seeds it.
    await result.current.refreshCurrentModel(true)
    expect($currentModel.get()).toBe('openai/gpt-5.5')

    await expect(
      result.current.selectModel({ model: 'gemini-3-pro', provider: 'google' })
    ).resolves.toBe(true)
    expect($currentModel.get()).toBe('gemini-3-pro')

    // Switching to Prism shows Prism's model, not Aura's — and the agent's own
    // cell outranks the profile default, so the backend is not even asked.
    const profileLookupsBefore = vi.mocked(getGlobalModelInfo).mock.calls.length

    rerender({ scope: agentScope('prism') })
    await result.current.refreshCurrentModel(true)

    expect($currentModel.get()).toBe('kimi-k2')
    expect($currentProvider.get()).toBe('moonshot')
    expect(vi.mocked(getGlobalModelInfo).mock.calls).toHaveLength(profileLookupsBefore)

    // Back to Aura: Aura kept its own pick.
    rerender({ scope: agentScope('aura') })
    await result.current.refreshCurrentModel(true)
    expect($currentModel.get()).toBe('gemini-3-pro')
  })

  it('keeps the desktop-quota branch local-only under any scope', async () => {
    quotaProvider.mockImplementation((slug: string) => slug === 'antigravity')
    const requestGateway = vi.fn()
    let controls!: Controls

    render(
      <Harness
        activeSessionId="session-1"
        isAgentSession
        scope={agentScope('aura')}
        onReady={value => (controls = value)}
        requestGateway={requestGateway}
      />
    )

    await expect(
      controls.selectModel({ model: 'gemini-3-pro', provider: 'antigravity' })
    ).resolves.toBe(true)

    // Still a pure early return: no config.set, no profile write, no error toast.
    expect(requestGateway).not.toHaveBeenCalled()
    expect(setGlobalModel).not.toHaveBeenCalled()
    expect(notifyError).not.toHaveBeenCalled()
    expect($currentModel.get()).toBe('gemini-3-pro')
    // ...it just lands in Aura's cell instead of the shared one.
    expect(window.localStorage.getItem('plobi.desktop.composer.model.agent:aura')).toBe('gemini-3-pro')
    expect(window.localStorage.getItem('plobi.desktop.composer.model')).toBeNull()
  })
})
