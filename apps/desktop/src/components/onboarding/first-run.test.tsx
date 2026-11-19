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
  it('步序就是主人 11-19 13:4x 拍的那一串（界面自述删掉、语言与外观合并）', () => {
    // 变异自检：把「setup」拆回语言／外观两屏，或把「interface」放回去 ⇒ 这条当场红。
    expect(FIRST_RUN_STEPS).toEqual([
      'welcome',
      'setup',
      'cards',
      'profile',
      'agentName',
      'userTitle',
      'done'
    ])
  })

  it('外观与语言合成一步：主题 ＋ 明暗 ＋ 语言（语言也是芯片）', async () => {
    await advanceTo('setup')

    expect(screen.getAllByText(/Appearance and language|外观与语言/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Theme$|主题/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Light or dark|明暗/).length).toBeGreaterThan(0)
    // 明暗三档：浅色 / 深色 / 跟随系统。
    expect(screen.getByRole('button', { name: /^Light$|浅色/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Dark$|深色/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Follow system|跟随系统/ })).toBeTruthy()
    // 语言也在同一块面板里，做成芯片（与画像那两题同一套语法）。
    expect(screen.getAllByText(/^Language$|语言/).length).toBeGreaterThan(0)
    expect(document.querySelectorAll('.fr-panel .fr-chip').length).toBeGreaterThanOrEqual(4)
  })

  it('三张卡＝左中右三张大卡片；点一张变成一屏滚动列表，里面是设置页那一张卡', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [{ id: 'claude-code', name: 'Claude Code' }] })
    getGlobalModelOptions.mockResolvedValue({
      providers: [
        { slug: 'openrouter', name: 'OpenRouter', auth_type: 'api_key', key_env: 'OPENROUTER_API_KEY' }
      ]
    })

    await advanceTo('cards')

    // 三张大卡片（名字直接读设置页那三栏）。
    expect(screen.getAllByText(/^Accounts$|^账号$/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/API keys|API 密钥/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Subscriptions$|订阅额度/).length).toBeGreaterThan(0)

    // 点「密钥」那一张 ⇒ 变成一屏滚动列表，每张卡就是设置页那一张（名字 ＋ ＋）。
    fireEvent.click(screen.getByText(/API keys|API 密钥/))
    await vi.waitFor(() => expect(screen.getAllByText('OpenRouter').length).toBeGreaterThan(0), {
      timeout: 4000
    })
    // ＋ 那颗的可达名字（设置页同款）。
    expect(screen.getByRole('button', { name: /Configure OpenRouter|配置 OpenRouter/ })).toBeTruthy()
  })

  it('三张卡是玻璃磨砂的大卡：标题＋一句简介＋左下角设置页那几家的品牌图标', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [{ id: 'claude-code', name: 'Claude Code' }] })
    getGlobalModelOptions.mockResolvedValue({
      providers: [
        { slug: 'openrouter', name: 'OpenRouter', auth_type: 'api_key', key_env: 'OPENROUTER_API_KEY' }
      ]
    })

    await advanceTo('cards')

    const cards = Array.from(document.querySelectorAll('.fr-lane'))

    expect(cards.length).toBe(3)

    for (const card of cards) {
      // 上面：名字 ＋ 一句简介；下面：左下角那排品牌图标 ＋ 右下角那颗箭头。
      expect(card.querySelector('.fr-lane-name')?.textContent?.trim()).toBeTruthy()
      expect(card.querySelector('.fr-lane-desc')?.textContent?.trim()).toBeTruthy()
      expect(card.querySelector('.fr-lane-marks')).toBeTruthy()
      expect(card.querySelector('.fr-lane-go')).toBeTruthy()
    }

    // 账号那张的左下角＝那一栏里那几家的品牌图标（设置页同一枚壳：ProviderBrand）。
    // 目录是异步回来的 ⇒ 等它到齐再断言（变异自检：把 marks 去掉 ⇒ 当场红）。
    await vi.waitFor(
      () => {
        const card = document.querySelectorAll('.fr-lane')[0]

        expect(card?.querySelectorAll('.fr-lane-marks > *').length ?? 0).toBeGreaterThan(0)
      },
      { timeout: 4000 }
    )
  })

  it('其余几屏的内容落进同一块玻璃面（与那三张卡同一套材质）', async () => {
    await advanceTo('setup')

    // 外观与语言那屏：内容落在玻璃面里。
    const panel = document.querySelector('.fr-panel')

    expect(panel).toBeTruthy()
    expect(panel?.querySelector('.fr-chips')).toBeTruthy()
    expect(panel?.querySelector('.fr-themes')).toBeTruthy()
  })

  it('那三张卡那屏不再有「和设置页分组一致」那句机制说明', async () => {
    listOAuthProviders.mockResolvedValue({ providers: [] })
    getGlobalModelOptions.mockResolvedValue({ providers: [] })

    await advanceTo('cards')

    // 标题下面只留那三张卡，标题本身不带说明句。
    expect(document.querySelector('.fr-cards-head .fr-title')?.textContent?.trim()).toBeTruthy()
    expect(document.querySelector('.fr-cards-head .fr-desc')).toBeNull()
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

    // 画像那两题＝**选项卡**（Qoder 那一路：芯片单选），不再是让人自己想词的空框。
    const purpose = screen.getByRole('button', { name: /^(Writing|写作|執筆)$/ })
    const domain = screen.getByRole('button', { name: /^(Teaching|教学|教育)$/ })

    expect(screen.queryByRole('textbox')).toBeNull()

    fireEvent.click(purpose)
    fireEvent.click(domain)
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
        purpose: (purpose.textContent ?? '').trim(),
        domain: (domain.textContent ?? '').trim(),
        user_title: 'Mr. Deng',
        agent_name: 'Xiaoman',
        agent_personality: ''
      })
    )
    expect(onCompleted).toHaveBeenCalled()
  })
})
