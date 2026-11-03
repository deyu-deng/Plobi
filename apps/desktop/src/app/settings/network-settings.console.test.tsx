import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getNetworkEgress = vi.fn()
const getNetworkImportClients = vi.fn()
const getNetworkStatus = vi.fn()
const getNetworkSubscription = vi.fn()
const importNetworkSubscription = vi.fn()
const saveNetworkEgress = vi.fn()

vi.mock('@/plobi', () => ({
  getNetworkEgress: () => getNetworkEgress(),
  //: 这一枚留在 mock 里是**有牙的**：这一屏不该再问它，谁把它接回去下面那条测试就红。
  getNetworkImportClients: () => getNetworkImportClients(),
  getNetworkStatus: () => getNetworkStatus(),
  getNetworkSubscription: () => getNetworkSubscription(),
  importNetworkPool: vi.fn(),
  importNetworkSubscription: (input: unknown) => importNetworkSubscription(input),
  saveNetworkEgress: (section: unknown) => saveNetworkEgress(section),
  setNetworkSystemProxy: vi.fn()
}))

import { NetworkSettings } from './network-settings'

//: 屏幕上的字来自 i18n 目录，断言也从目录取：换语言不许把测试换红。
import { TRANSLATIONS } from '@/i18n/catalog'

const copy = TRANSLATIONS.en.settings.network

const LONG = '🇺🇲 美国W01 | IEPL | x1.5'

const RECORD = {
  declared: true,
  network: {
    egress: {
      engine: { binary: '', enabled: true, mixed_port: 7895 },
      groups: {
        POOL: { type: 'url-test', include: ['🇭🇰 香港W01', '🇯🇵 日本W01'] },
        香港: { type: 'select', include: ['🇭🇰 香港W01'] }
      },
      nodes: {
        '🇭🇰 香港W01': { type: 'ss', server: 'hk.example', port: 1 },
        [LONG]: { type: 'ss', server: 'us.example', port: 1 }
      },
      rules: [
        { match: 'geosite:cn', via: 'direct' },
        { match: '*', via: 'proxy:POOL' }
      ],
      system_proxy: { enabled: true }
    }
  }
}

//: 故意只给最少字段：后端少一个键就把整屏炸掉过一次（`status.rules.find`），这条继续钉那处防御。
const STATUS = {
  engine: {
    detail: 'started by Plobi',
    mixed_port: 7895,
    probe: '204 via http://127.0.0.1:7895',
    state: 'running-ours'
  },
  system_proxy: { backup_exists: true, declared: true, ours: true, supported: true }
}

beforeEach(() => {
  getNetworkEgress.mockResolvedValue(RECORD)
  getNetworkStatus.mockResolvedValue(STATUS)
  getNetworkSubscription.mockResolvedValue({ saved: true })
  importNetworkSubscription.mockResolvedValue({ groups: {}, nodes: {}, notes: [], ok: true })
  saveNetworkEgress.mockResolvedValue({ engine: { detail: 'ok', state: 'running-ours' }, ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

//: 用户 11-01 第 1、2 条把这屏改成了**只剩编辑**：导入成员 + 配规则。
//: 开关、选节点、TUN/系统代理全搬进左下底栏那枚图标点出来的面板（那两屏由 `egress-panel.test.tsx` 判）。
describe('设置里那一屏只剩编辑', () => {
  it('控制按键不在这一屏：没有载体开关、没有默认出口卡、没有模式分段', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByDisplayValue(LONG)).toBeTruthy())

    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByText('当前默认出口')).toBeNull()
    expect(screen.queryByRole('button', { name: copy.modeGlobal })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.modeRule })).toBeNull()
    // 那排读数（引擎/成员/组/规则/接管）也走了：状态由底栏图标的颜色说，句子留在屏上正是"太多文字"。
    expect(screen.queryByText(/running-ours ·/)).toBeNull()
  })

  it('后端少给一格也不炸屏，且不许冒出半截句子', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByDisplayValue('geosite:cn')).toBeTruthy())

    expect(screen.queryByText(/引擎会判|表达不了/)).toBeNull()
  })

  it('长名字整条在自己的输入框里，不是被裁掉的芯片（用户 11-01「文字信息也被遮挡了」）', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByDisplayValue(LONG)).toBeTruthy())

    const input = screen.getByDisplayValue(LONG)
    expect(input.tagName).toBe('INPUT')
    expect(screen.getAllByDisplayValue('🇭🇰 香港W01').length).toBeGreaterThan(0)
    // 旧的形状是 `truncate` + 定高滚动容器：值还在 DOM 里但屏幕上看不见，等于没有这条。
    expect(input.className).not.toMatch(/truncate/)
    expect(screen.queryByRole('button', { name: LONG })).toBeNull()
  })

  it('粘贴一段配置也能导入——与链接、文件同一个解析器', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByDisplayValue(LONG)).toBeTruthy())

    const pasted = ['proxies:', '  - {name: a, type: ss, server: x.example, port: 1}'].join('\n')
    fireEvent.change(screen.getByPlaceholderText(copy.pasteHint), { target: { value: pasted } })
    fireEvent.click(screen.getByRole('button', { name: copy.importPasted }))

    await waitFor(() =>
      expect(importNetworkSubscription).toHaveBeenCalledWith(
        expect.objectContaining({ text: expect.stringContaining('proxies:') })
      )
    )
  })

  it('没读到表之前保存点不动：空草稿覆盖整张表这件事在这台发生过（10-29）', async () => {
    getNetworkEgress.mockRejectedValue(new Error('读不到'))
    render(<NetworkSettings />)

    await waitFor(() => expect(screen.getByText(copy.notLoaded)).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: copy.save }))

    expect(saveNetworkEgress).not.toHaveBeenCalled()
  })

  it('存过链接之后「更新」只要一个动词：不必再粘一遍', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect((screen.getByRole('button', { name: copy.refreshSub }) as HTMLButtonElement).disabled).toBe(false))

    fireEvent.click(screen.getByRole('button', { name: copy.refreshSub }))

    await waitFor(() => expect(importNetworkSubscription).toHaveBeenCalledWith({ keepGroups: true, refresh: true }))
  })

  it('没存过链接时「更新」是灰的——不是点下去才报没有', async () => {
    getNetworkSubscription.mockResolvedValue({ saved: false })
    render(<NetworkSettings />)

    await waitFor(() => expect((screen.getByRole('button', { name: copy.refreshSub }) as HTMLButtonElement).disabled).toBe(true))
    expect(importNetworkSubscription).not.toHaveBeenCalled()
  })

  it('「从本机客户端缓存导入」不在这一屏，连问都不问（用户 11-03 判死这一型）', async () => {
    getNetworkImportClients.mockResolvedValue({ clients: [{ id: 'ikuuu', note: 'iKuuu', present: true }] })
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByDisplayValue(LONG)).toBeTruthy())

    // 读别的软件自己拉的缓存当配置来源 = 把别人的存储格式当自己的契约：它一变，这台就静默没节点。
    expect(screen.queryByText(/client’s own cache/i)).toBeNull()
    expect(screen.queryByText(/客户端的缓存|客戶端的快取|キャッシュ/)).toBeNull()
    expect(getNetworkImportClients).not.toHaveBeenCalled()
  })
})
