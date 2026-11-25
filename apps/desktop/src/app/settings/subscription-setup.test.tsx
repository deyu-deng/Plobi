/**
 * 「点配置就自动装」——设置页里那半张卡片的契约。
 *
 * 卡片列表跟着 magpie 的 provider 列表走，插件被卸掉时卡片**留着**（账号是特意保留的），
 * 差别只在 `state`：`not_installed` + 一句安装命令。这时用户点「+」打开面板，第一步本来
 * 就是把它装回来，不该再让他按第二次（用户 11-21：「支持用户点击配置的按键，如果插件
 * 没下载会自动下载」）。装没装成由 catalog 复读说话，这半张卡片只负责把命令跑一次。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as i18nModule from '@/i18n'

import type { LoginCard } from './quota-login-cards'
import { SubscriptionSetupDialog } from './subscription-setup'

// 文案用真字典（zh），不参与断言：面板会渲染按钮文字，假对象撑不住这一层。
vi.mock('@/i18n', async () => {
  const actual = await vi.importActual<typeof i18nModule>('@/i18n')

  return {
    ...actual,
    useI18n: () => ({ locale: 'zh', setLocale: () => {}, t: actual.TRANSLATIONS.zh })
  }
})

const install = vi.fn()

function card(overrides: Partial<LoginCard> = {}): LoginCard {
  return {
    aggregate: true,
    canLogin: true,
    connectable: true,
    connected: false,
    detail: '',
    download: null,
    id: 'qoder',
    installCommand: 'magpie plugin add @magpie-community/opencode-qoder-auth@latest',
    installKind: 'plugin',
    introKey: 'qoder',
    loginMethod: 'oauth',
    name: 'Qoder',
    pending: false,
    reasonCode: '',
    state: 'not_installed',
    appKind: 'cli',
    ...overrides
  }
}

function mount(target: LoginCard, onChanged = vi.fn()) {
  return render(<SubscriptionSetupDialog card={target} onChanged={onChanged} onClose={vi.fn()} open />)
}

beforeEach(() => {
  install.mockReset()
  install.mockResolvedValue({ ok: true, installed: true })

  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { plobiGateway: { install } }
  })
})

afterEach(() => {
  cleanup()
  Reflect.deleteProperty(window, 'plobiDesktop')
})

describe('subscription setup — a card whose plugin is gone', () => {
  it('runs the install command by itself when the card is opened', async () => {
    mount(card())

    await waitFor(() => expect(install).toHaveBeenCalledTimes(1))
    expect(install).toHaveBeenCalledWith('qoder')
  })

  it('re-reads the catalog once the install reports the plugin back', async () => {
    const onChanged = vi.fn()

    mount(card(), onChanged)

    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('does not loop: reopening the same card installs once', async () => {
    const view = mount(card())

    await waitFor(() => expect(install).toHaveBeenCalledTimes(1))
    view.rerender(
      <SubscriptionSetupDialog card={card()} onChanged={vi.fn()} onClose={vi.fn()} open />
    )
    await waitFor(() => expect(install).toHaveBeenCalledTimes(1))
  })

  it('stays put when there is no command to run — a bare leg is not auto-installed', async () => {
    mount(card({ installCommand: null, installKind: null }))

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(install).not.toHaveBeenCalled()
  })

  it('stays put when the plugin is already on the machine', async () => {
    mount(card({ state: 'signed_out', installCommand: null, installKind: null }))

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(install).not.toHaveBeenCalled()
  })
})

describe('subscription setup — a plugin that offers more than one way in', () => {
  /**
   * 2026-11-25：22 家的插件各自报了自己的登录通道（浏览器 / 贴 key / 设备码）。
   * 只给一个按钮等于替用户砍掉另一半 —— qoder-cn 报 4 条、zcode 报 5 条，而它们默认
   * 那条常常要订阅，用户手上却只有一把 key。
   */
  const lanes = [
    { label: 'Sign in with Qoder CN', type: 'oauth' },
    { label: 'Qoder CN API key', placeholder: 'ak_…', type: 'api' }
  ]

  const signedOut = () =>
    card({ appKind: 'cli', installCommand: null, installKind: null, methods: lanes, state: 'signed_out' })

  function withAuth(auth = vi.fn().mockResolvedValue({ ok: true })) {
    Object.defineProperty(window, 'plobiDesktop', {
      configurable: true,
      value: { plobiGateway: { install, auth } }
    })

    return auth
  }

  it('offers one button per lane, in their own words', () => {
    withAuth()
    mount(signedOut())

    expect(screen.getByRole('button', { name: lanes[0].label })).toBeTruthy()
    expect(screen.getByRole('button', { name: lanes[1].label })).toBeTruthy()
  })

  it('hands the picked lane to the hub, not the default one', async () => {
    const auth = withAuth()
    mount(signedOut())

    fireEvent.click(screen.getByRole('button', { name: lanes[1].label }))

    await waitFor(() => expect(auth).toHaveBeenCalledWith('qoder', lanes[1].label))
  })

  it('keeps the single button when the plugin only knows one way in', () => {
    const auth = withAuth()
    mount(card({ appKind: 'cli', installCommand: null, installKind: null, methods: [lanes[0]], state: 'signed_out' }))

    expect(screen.queryByRole('button', { name: lanes[0].label })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /登录|Sign in/ }))

    return waitFor(() => expect(auth).toHaveBeenCalledWith('qoder', undefined))
  })
})
