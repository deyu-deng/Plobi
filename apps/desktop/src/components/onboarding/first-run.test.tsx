import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const listOAuthProviders = vi.fn()
const getGlobalModelOptions = vi.fn()
const setOnboardingProfile = vi.fn()

vi.mock('@/plobi', () => ({
  getGlobalModelOptions: (opts: unknown) => getGlobalModelOptions(opts),
  listOAuthProviders: () => listOAuthProviders(),
  setOnboardingProfile: (body: unknown) => setOnboardingProfile(body)
}))

import { FIRST_RUN_STEPS, FirstRunFlow } from './first-run'

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderFlow(props: Partial<React.ComponentProps<typeof FirstRunFlow>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <FirstRunFlow ctx={{ requestGateway: vi.fn() as never }} {...props} />
    </QueryClientProvider>
  )
}

/** 走到指定那一步：欢迎屏自己走完（1.4s），其余一步步按。 */
async function advanceTo(step: string) {
  renderFlow()
  await vi.waitFor(
    () => expect(screen.getByRole('button', { name: /Continue|继续/ })).toBeTruthy(),
    { timeout: 4000 }
  )

  for (const name of FIRST_RUN_STEPS) {
    if (name === step) {
      return
    }

    fireEvent.click(screen.getByRole('button', { name: /Continue|继续/ }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: /Continue|继续/ })).toBeTruthy(), {
      timeout: 4000
    })
  }
}

describe('首装那几步（WP-ONBOARDING-WELCOME-FLOW）', () => {
  it('步序就是主人 11-17 20:0x 说的那一串（模型配置并进三张卡那一屏）', () => {
    // 变异自检：把「语言」挪到「外观」后面 ⇒ 这条当场红。
    expect(FIRST_RUN_STEPS).toEqual([
      'welcome',
      'interface',
      'language',
      'appearance',
      'cards',
      'profile',
      'agentName',
      'userTitle',
      'done'
    ])
  })

  it('外观那一步同时给主题与明暗（设置页「外观」那一栏就是这两样）', async () => {
    await advanceTo('appearance')

    expect(screen.getAllByText(/Appearance|外观/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Theme$|主题/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Light or dark|明暗/).length).toBeGreaterThan(0)
    // 明暗三档：浅色 / 深色 / 跟随系统。
    expect(screen.getByRole('button', { name: /^Light$|浅色/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Dark$|深色/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Follow system|跟随系统/ })).toBeTruthy()
  })

  it('三张卡＝模型配置那一屏，名字直接读设置页那三栏，里面列的是可配置的目录', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [{ id: 'claude-code', name: 'Claude Code' }] })
    getGlobalModelOptions.mockResolvedValue({
      providers: [
        { slug: 'openrouter', name: 'OpenRouter', auth_type: 'api_key', key_env: 'OPENROUTER_API_KEY' }
      ]
    })

    await advanceTo('cards')

    expect(screen.getAllByText(/^Accounts$|^账号$/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/API keys|API 密钥/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Subscriptions$|订阅额度/).length).toBeGreaterThan(0)
    // 卡里列的是**可配置的目录**，不是「已经配好的」⇒ 一个都没配时照样列得出来。
    await vi.waitFor(() => expect(screen.getAllByText('Claude Code').length).toBeGreaterThan(0), {
      timeout: 4000
    })
    await vi.waitFor(() => expect(screen.getAllByText('OpenRouter').length).toBeGreaterThan(0), {
      timeout: 4000
    })
  })

  it('末步把画像与称呼交给后端（USER.md），把名字与性格也交过去（SOUL.md）', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [] })
    getGlobalModelOptions.mockResolvedValue({ providers: [] })
    setOnboardingProfile.mockResolvedValue({ ok: true })
    const onCompleted = vi.fn()
    renderFlow({ onCompleted, startAt: 'profile' })

    await vi.waitFor(() => expect(screen.getAllByText(/Two questions|两个问题/).length).toBeGreaterThan(0), {
      timeout: 4000
    })

    fireEvent.change(screen.getByLabelText(/What do you mainly use Plobi for|你主要拿 Plobi 做什么/), {
      target: { value: 'writing papers' }
    })
    fireEvent.change(screen.getByLabelText(/What is your field|你的工作领域/), {
      target: { value: 'physics' }
    })
    fireEvent.click(screen.getByRole('button', { name: /Continue|继续/ }))

    await vi.waitFor(() => expect(screen.getAllByText(/Give it a name|给它取个名字/).length).toBeGreaterThan(0), {
      timeout: 4000
    })
    fireEvent.change(screen.getByLabelText(/Give it a name|给它取个名字/), { target: { value: 'Xiaoman' } })
    fireEvent.click(screen.getByRole('button', { name: /Continue|继续/ }))

    await vi.waitFor(
      () => expect(screen.getAllByText(/What should it call you|该怎么称呼你/).length).toBeGreaterThan(0),
      { timeout: 4000 }
    )
    fireEvent.change(screen.getByLabelText(/What should it call you|该怎么称呼你/), { target: { value: 'Mr. Deng' } })
    fireEvent.click(screen.getByRole('button', { name: /Continue|继续/ }))

    await vi.waitFor(() => expect(screen.getAllByText(/All set|好了/).length).toBeGreaterThan(0), {
      timeout: 4000
    })
    fireEvent.click(screen.getByRole('button', { name: /Start|开始用/ }))

    await vi.waitFor(() =>
      expect(setOnboardingProfile).toHaveBeenCalledWith({
        purpose: 'writing papers',
        domain: 'physics',
        user_title: 'Mr. Deng',
        agent_name: 'Xiaoman',
        agent_personality: ''
      })
    )
    expect(onCompleted).toHaveBeenCalled()
  })
})
