import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'

import { TRANSLATIONS } from '@/i18n/catalog'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getNetworkEgress = vi.fn()
const getNetworkStatus = vi.fn()
const saveNetworkEgress = vi.fn()
const setNetworkSystemProxy = vi.fn()

vi.mock('@/plobi', () => ({
  getNetworkEgress: () => getNetworkEgress(),
  getNetworkStatus: () => getNetworkStatus(),
  saveNetworkEgress: (input: unknown) => saveNetworkEgress(input),
  setNetworkSystemProxy: (enabled: boolean) => setNetworkSystemProxy(enabled)
}))

import { EgressPanel, EgressRailIcon, egressRailItem } from './egress-panel'

//: 屏幕上的字来自目录，测试也从目录取——换语言不许把测试换红。
const copy = TRANSLATIONS.en.settings.network

const BLOCK = {
  engine: { enabled: true, mixed_port: 7895, tun: { 'allow-foreign': false, enabled: false, name: 'PlobiTun', stack: 'gvisor' } },
  groups: { POOL: { include: ['🇯🇵 日本W01'], type: 'url-test' } },
  nodes: { '🇯🇵 日本W01': { port: 1, server: 'jp.example', type: 'ss' } },
  rules: [
    { match: 'geosite:cn', via: 'direct' },
    { match: '*', via: 'proxy:POOL' }
  ],
  system_proxy: { enabled: true }
}

const record = { declared: true, network: { egress: BLOCK } }

const status = (over: Record<string, unknown> = {}) => ({
  engine: { detail: 'started by Plobi', state: 'running-ours' },
  membership: { groups: { POOL: ['🇯🇵 日本W01'] }, nodes: ['🇯🇵 日本W01'] },
  switch: { exit: 'POOL', mode: 'rule', transport: 'system', vpn: true },
  system_proxy: { declared: true, ours: true, supported: true },
  tun: { block: { 'allow-foreign': false, name: 'PlobiTun', stack: 'gvisor' }, ok: true, reasons: [], requested: false },
  ...over
})

const sent = () => saveNetworkEgress.mock.calls[saveNetworkEgress.mock.calls.length - 1][0]

beforeEach(() => {
  getNetworkEgress.mockResolvedValue(record)
  getNetworkStatus.mockResolvedValue(status())
  saveNetworkEgress.mockResolvedValue({ engine: { detail: 'ok', state: 'running-ours' }, ok: true })
  setNetworkSystemProxy.mockResolvedValue({ enabled: true, ok: true, proxy: { detail: 'ok', state: 'applied' } })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('底栏那个面板的控制屏', () => {
  it('摆的是后端那一格读数，不是自己从规则里再推一遍', async () => {
    // `switch` 说 system/vpn=on，而规则里那条分类直连已被抹掉：面板必须听 `switch`。
    getNetworkStatus.mockResolvedValue(
      status({ ...status(), switch: { exit: 'POOL', mode: 'global', transport: 'system', vpn: true } })
    )
    render(<EgressPanel />)

    await waitFor(() => expect(screen.getByRole('button', { name: copy.powerOff })).toBeTruthy())
    expect(screen.getByRole('button', { name: copy.transportSystem }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: copy.modeGlobal }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('POOL')).toBeTruthy()
  })

  it('点 TUN：送出守卫给的那块网卡，并把注册表那一半交还', async () => {
    render(<EgressPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: copy.powerOff })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: copy.transportTun }))

    await waitFor(() => expect(saveNetworkEgress).toHaveBeenCalled())
    const egress = sent().egress
    expect(egress.engine.tun).toEqual({ 'allow-foreign': false, enabled: true, name: 'PlobiTun', stack: 'gvisor' })
    expect(egress.system_proxy).toEqual({ enabled: false })
    expect(setNetworkSystemProxy).toHaveBeenCalledWith(false)
    // 成员与规则整段带着走：保存面是替换语义，少带一个键等于删掉一个键。
    expect(Object.keys(egress.nodes)).toEqual(['🇯🇵 日本W01'])
    expect(egress.rules).toHaveLength(2)
  })

  it('守卫拒了 TUN：屏上只有一枚警告，解释留在悬停里', async () => {
    getNetworkStatus.mockResolvedValue(
      status({
        ...status(),
        tun: {
          block: { name: 'PlobiTun', stack: 'gvisor' },
          ok: false,
          reasons: ['这个进程不是管理员 —— TUN 要建网卡就得提权', '场上没有 wintun.dll —— TUN 网卡建不出来'],
          requested: false
        }
      })
    )
    render(<EgressPanel />)

    await waitFor(() => expect(screen.getByTestId('tun-warning')).toBeTruthy())
    expect(screen.getByTestId('tun-warning').getAttribute('title')).toContain('wintun.dll')
    // 「不要乱写文案」那条判据：机制解释不许成为正文。
    expect(screen.queryByText(/管理员/)).toBeNull()
    expect(screen.queryByText(/wintun/)).toBeNull()
  })

  it('表说要接管而注册表不是我们的：警告，而不是把开关画成绿的', async () => {
    getNetworkStatus.mockResolvedValue(status({ system_proxy: { declared: true, ours: false, supported: true } }))
    render(<EgressPanel />)

    await waitFor(() => expect(screen.getByTestId('proxy-warning')).toBeTruthy())
    expect(screen.getByTestId('proxy-warning').getAttribute('title')).toBe(copy.proxyDeclaredNotApplied)
  })

  it('接管被拒那句话要上屏——它是解析器/监督在说话，不是装饰', async () => {
    setNetworkSystemProxy.mockResolvedValue({
      enabled: false,
      ok: false,
      proxy: { detail: '接管前那份备份读不出来', state: 'refused' }
    })
    render(<EgressPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: copy.powerOff })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: copy.powerOff }))

    await waitFor(() => expect(screen.getByText('接管前那份备份读不出来')).toBeTruthy())
  })
})

describe('底栏那个面板的节点屏', () => {
  it('卡片名字完整在屏上：不截断、不裁高度（用户 11-01「节点的文字信息也被遮挡了」）', async () => {
    const long = '🇺🇲 美国W01 | IEPL | x1.5'
    getNetworkStatus.mockResolvedValue(
      status({ ...status(), membership: { groups: { POOL: [long] }, nodes: [long] } })
    )
    render(<EgressPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: copy.tabNodes })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: copy.tabNodes }))

    const card = await screen.findByRole('button', { name: new RegExp(long.slice(0, 6)) })
    expect(card.textContent).toContain(long)
    expect(card.className).not.toMatch(/truncate/)
    expect([...card.querySelectorAll('span')].every(span => !span.className.includes('truncate'))).toBe(true)
  })

  it('点一张卡片就把兜底规则指过去，别的规则一条不碰', async () => {
    render(<EgressPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: copy.tabNodes })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: copy.tabNodes }))
    fireEvent.click(await screen.findByRole('button', { name: /日本W01/ }))

    await waitFor(() => expect(saveNetworkEgress).toHaveBeenCalled())
    expect(sent().egress.rules).toEqual([
      { match: 'geosite:cn', via: 'direct' },
      { match: '*', via: 'proxy:🇯🇵 日本W01' }
    ])
    // 选节点不动载体：系统代理那半照原样带着，也不碰注册表。
    expect(sent().egress.system_proxy).toEqual({ enabled: true })
    expect(setNetworkSystemProxy).not.toHaveBeenCalled()
  })
})

describe('拨不过去与没读到表', () => {
  it('守卫拒了 TUN：表退回拨之前，注册表回到原来的载体，屏上给那一条出口', async () => {
    saveNetworkEgress
      .mockResolvedValueOnce({
        engine: { detail: 'TUN 被守卫拒了：场上已经有别人的 TUN 类网卡（Tailscale）', state: 'failed' },
        ok: true
      })
      .mockResolvedValueOnce({ engine: { detail: 'ok', state: 'running-ours' }, ok: true })
    render(<EgressPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: copy.powerOff })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: copy.transportTun }))

    await waitFor(() => expect(screen.getByRole('button', { name: copy.allowForeignRetry })).toBeTruthy())
    // 第二次写盘必须把**原样**放回去：表里写着 TUN 而机器上什么都没有，就是 10-29 那次事故的形状。
    expect(sent().egress.engine.tun.enabled).toBe(false)
    expect(setNetworkSystemProxy).toHaveBeenLastCalledWith(true)
    expect(saveNetworkEgress).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: copy.allowForeignRetry }))
    await waitFor(() => expect(saveNetworkEgress).toHaveBeenCalledTimes(3))
    expect(sent().egress.engine.tun).toMatchObject({ 'allow-foreign': true, enabled: true })
  })

  it('桥不在的时候同步抛不许崩掉整棵树（11-01 这台：preload 被抹掉 ⇒ 整屏 Something went wrong）', async () => {
    getNetworkStatus.mockImplementation(() => {
      throw new TypeError("Cannot read properties of undefined (reading 'api')")
    })
    getNetworkEgress.mockImplementation(() => {
      throw new TypeError("Cannot read properties of undefined (reading 'api')")
    })

    const view = render(<div>
      <EgressRailIcon />
      <EgressPanel />
    </div>)

    // 不崩：图标还在，面板还在，只是没读到——控件因此是禁用的。
    await waitFor(() => expect(view.getByTestId('egress-panel')).toBeTruthy())
    expect(view.getByRole('button', { name: copy.powerOn }).getAttribute('disabled')).not.toBeNull()
    expect(saveNetworkEgress).not.toHaveBeenCalled()
  })

  it('没读到表的时候一张卡都点不动，一次保存都不许发生', async () => {
    getNetworkEgress.mockRejectedValue(new Error('boom'))
    render(<EgressPanel />)

    await waitFor(() => expect(screen.getByTestId('egress-panel')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: copy.transportTun }))
    fireEvent.click(screen.getByRole('button', { name: copy.tabNodes }))

    await waitFor(() => expect(screen.getByTestId('egress-panel')).toBeTruthy())
    expect(saveNetworkEgress).not.toHaveBeenCalled()
    expect(setNetworkSystemProxy).not.toHaveBeenCalled()
  })
})

describe('左下底栏那一格', () => {
  it('是一枚带面板的动作图标：点开的就是那两屏', () => {
    const item = egressRailItem(copy.title)

    expect(item.id).toBe('egress')
    expect(item.variant).toBe('menu')
    expect(typeof item.menuContent).toBe('function')
    // 底栏那一排是图标条：名字给 tooltip，屏幕上不占字。
    expect(item.title).toBe(copy.title)
    expect(item.label).toBeUndefined()

    const view = render(<div>{(item.menuContent as (close: () => void) => ReactNode)(() => {})}</div>)
    expect(view.getByTestId('egress-panel')).toBeTruthy()
  })

  it('宽度只有一个来源：弹窗跟着走，面板按屏给', () => {
    // 11-01 用户两次截图：先是「右边被裁」（两处都写 `w-80`，里面一撑宽就裁），
    // 后是「一行两张太松」。所以宽度收到面板这一格，并且**两屏不一样**。
    const item = egressRailItem(copy.title)
    expect(item.menuClassName).toContain('w-auto')
    expect(item.menuClassName).not.toMatch(/(^|\s)w-(56|64|72|80|96)(\s|$)/)

    const view = render(<div>{(item.menuContent as (close: () => void) => ReactNode)(() => {})}</div>)
    const panel = view.getByTestId('egress-panel')
    expect(panel.className).toContain('w-96')
    expect(panel.className).toContain('min-w-0')
  })

  it('节点屏一行三张：一行两张在这个宽度上显得空', async () => {
    render(<EgressPanel />)
    fireEvent.click(await screen.findByRole('button', { name: copy.tabNodes }))

    const panel = screen.getByTestId('egress-panel')
    expect(panel.className).toContain('w-[32rem]')

    const grid = screen.getByRole('button', { name: copy.panelAll }).parentElement?.nextElementSibling
    expect(grid?.className).toContain('grid-cols-3')
  })

  it('载体那两颗按钮不换行：窄了就省略，不许把面板撑破', async () => {
    render(<EgressPanel />)
    const button = await screen.findByRole('button', { name: copy.transportSystem })

    expect(button.className).toContain('whitespace-nowrap')
    expect(button.className).toContain('min-w-0')
    expect(button.className).toContain('truncate')
  })
})
