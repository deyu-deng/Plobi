/**
 * 「点配置就自动装」——设置页里那半张卡片的契约。
 *
 * 卡片列表跟着 magpie 的 provider 列表走，插件被卸掉时卡片**留着**（账号是特意保留的），
 * 差别只在 `state`：`not_installed` + 一句安装命令。这时用户点「+」打开面板，第一步本来
 * 就是把它装回来，不该再让他按第二次（用户 11-21：「支持用户点击配置的按键，如果插件
 * 没下载会自动下载」）。装没装成由 catalog 复读说话，这半张卡片只负责把命令跑一次。
 */
import { cleanup, render, waitFor } from '@testing-library/react'
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
