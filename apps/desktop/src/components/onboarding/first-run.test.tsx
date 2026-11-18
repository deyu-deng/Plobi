import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const listOAuthProviders = vi.fn()
const setOnboardingProfile = vi.fn()

vi.mock('@/plobi', () => ({
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
      <FirstRunFlow
        ctx={{ requestGateway: vi.fn() as never }}
        renderModel={() => <div>provider picker</div>}
        {...props}
      />
    </QueryClientProvider>
  )
}

describe('首装那十步（WP-ONBOARDING-WELCOME-FLOW）', () => {
  it('步序就是主人 11-17 20:0x 说的那一串', () => {
    // 变异自检：把「语言」挪到「主题」后面 ⇒ 这条当场红。
    expect(FIRST_RUN_STEPS).toEqual([
      'welcome',
      'interface',
      'language',
      'theme',
      'cards',
      'model',
      'profile',
      'agentName',
      'userTitle',
      'done'
    ])
  })

  it('三张卡的名字直接读设置页那三栏（两处不许各说各话）', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [{ id: 'claude-code', name: 'Claude Code' }] })
    renderFlow()

    // 欢迎那屏自己走完 ⇒ 界面 → 语言 → 主题 → 三张卡。
    await screen.findByText('Setting up Plobi')
    await vi.waitFor(() => expect(screen.getByText('This is the screen you will use every day')).toBeTruthy(), { timeout: 4000 })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await vi.waitFor(() => expect(screen.getByText('Pick a language')).toBeTruthy(), { timeout: 4000 })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await vi.waitFor(() => expect(screen.getByText('Pick a theme')).toBeTruthy(), { timeout: 4000 })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await vi.waitFor(() => expect(screen.getByText('Where to start')).toBeTruthy(), { timeout: 4000 })

    // 三个名字＝设置页那三栏（账号 / API 密钥 / 订阅额度）。
    expect(screen.getAllByText('Accounts').length).toBeGreaterThan(0)
    expect(screen.getAllByText('API keys').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Subscriptions').length).toBeGreaterThan(0)
  })

  it('「三张卡」按继续 ⇒ 交回既有的 provider 选择器去配模型', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [{ id: 'claude-code', name: 'Claude Code' }] })
    const onModelStep = vi.fn()
    renderFlow({ onModelStep })

    await vi.waitFor(() => expect(screen.getByText('Setting up Plobi')).toBeTruthy(), { timeout: 4000 })
    await vi.waitFor(() => expect(screen.getByText('This is the screen you will use every day')).toBeTruthy(), { timeout: 4000 })

    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
      await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeTruthy(), { timeout: 4000 })
    }

    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(onModelStep).toHaveBeenCalled()
  })

  it('末步把画像与称呼交给后端（USER.md），把名字与性格也交过去（SOUL.md）', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [{ id: 'claude-code', name: 'Claude Code' }] })
    setOnboardingProfile.mockResolvedValue({ ok: true })
    const onCompleted = vi.fn()
    renderFlow({ onCompleted, startAt: 'profile' })

    await vi.waitFor(() => expect(screen.getByText('Two questions')).toBeTruthy(), { timeout: 4000 })

    fireEvent.change(screen.getByLabelText('What do you mainly use Plobi for?'), { target: { value: '写论文' } })
    fireEvent.change(screen.getByLabelText('What is your field?'), { target: { value: '理论物理' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    await vi.waitFor(() => expect(screen.getAllByText('Give it a name').length).toBeGreaterThan(0), { timeout: 4000 })
    fireEvent.change(screen.getByLabelText('Give it a name'), { target: { value: '小满' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    await vi.waitFor(() => expect(screen.getAllByText('What should it call you').length).toBeGreaterThan(0), { timeout: 4000 })
    fireEvent.change(screen.getByLabelText('What should it call you'), { target: { value: '邓老师' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    await vi.waitFor(() => expect(screen.getAllByText('All set').length).toBeGreaterThan(0), { timeout: 4000 })
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))

    await vi.waitFor(() =>
      expect(setOnboardingProfile).toHaveBeenCalledWith({
        purpose: '写论文',
        domain: '理论物理',
        user_title: '邓老师',
        agent_name: '小满',
        agent_personality: ''
      })
    )
    expect(onCompleted).toHaveBeenCalled()
  })
})
