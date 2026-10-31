import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getNetworkEgress = vi.fn()
const getNetworkImportClients = vi.fn()
const getNetworkStatus = vi.fn()
const getNetworkSubscription = vi.fn()
const importNetworkSubscription = vi.fn()

vi.mock('@/plobi', () => ({
  getNetworkEgress: () => getNetworkEgress(),
  getNetworkImportClients: () => getNetworkImportClients(),
  getNetworkStatus: () => getNetworkStatus(),
  getNetworkSubscription: () => getNetworkSubscription(),
  importNetworkPool: vi.fn(),
  importNetworkSubscription: (input: unknown) => importNetworkSubscription(input),
  saveNetworkEgress: vi.fn(),
  setNetworkSystemProxy: vi.fn()
}))

import { NetworkSettings } from './network-settings'

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
        '🇯🇵 日本W01': { type: 'ss', server: 'jp.example', port: 1 }
      },
      rules: [
        { match: 'geosite:cn', via: 'direct' },
        { match: '*', via: 'proxy:POOL' }
      ],
      system_proxy: { enabled: true }
    }
  }
}

//: 故意只给最少字段：后端少一个键就把整屏炸掉过一次（`status.rules.find`），这条钉住那处防御。
const STATUS = {
  engine: {
    detail: 'started by Plobi',
    mixed_port: 7895,
    probe: '204 via http://127.0.0.1:7895',
    state: 'running-ours'
  },
  system_proxy: {
    backup_exists: true,
    declared: true,
    ours: true,
    pac_path: 'D:/plobi/plobi-egress.pac',
    supported: true
  }
}

beforeEach(() => {
  getNetworkEgress.mockResolvedValue(RECORD)
  getNetworkStatus.mockResolvedValue(STATUS)
  getNetworkImportClients.mockResolvedValue({ clients: [] })
  getNetworkSubscription.mockResolvedValue({ saved: true })
  importNetworkSubscription.mockResolvedValue({ groups: {}, nodes: {}, notes: [], ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('代理（VPN）那一屏的简约控制台', () => {
  it('把兜底规则指的出口摆在正面，点一个成员就换过去（不点保存不写盘）', async () => {
    render(<NetworkSettings />)

    // 兜底规则 `* → proxy:POOL` ⇒ 卡上就该写着 POOL
    await waitFor(() => expect(screen.getByText('当前默认出口')).toBeTruthy())
    expect(screen.getByText('POOL', { selector: 'span' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '🇯🇵 日本W01' }))

    expect(screen.getByText('🇯🇵 日本W01', { selector: 'span' })).toBeTruthy()
    expect(screen.queryByText('POOL', { selector: 'span' })).toBeNull()
  })

  it('「全部走代理」把 geosite:cn 那条直连摘掉，「按规则」再补回来', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByText('当前默认出口')).toBeTruthy())

    expect(screen.getByDisplayValue('geosite:cn')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '全部走代理' }))
    expect(screen.queryByDisplayValue('geosite:cn')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '按规则' }))
    expect(screen.getByDisplayValue('geosite:cn')).toBeTruthy()
  })

  it('编辑器收在「高级」里，不展开就不占地方', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByText('当前默认出口')).toBeTruthy())

    const details = screen.getByText('高级：出口 / 规则 / 成员表 / 组 / 导入 / 引擎').closest('details')
    expect(details).toBeTruthy()
    expect(details?.open).toBe(false)
  })

  it('粘贴一段配置也能导入——用户问的第二种方式', async () => {
    render(<NetworkSettings />)
    await waitFor(() => expect(screen.getByText('当前默认出口')).toBeTruthy())

    const pasted = ['proxies:', '  - {name: a, type: ss, server: x.example, port: 1}'].join('\n')
    fireEvent.change(screen.getByPlaceholderText(/粘在这里/), { target: { value: pasted } })
    fireEvent.click(screen.getByRole('button', { name: '导入粘贴的配置' }))

    await waitFor(() =>
      expect(importNetworkSubscription).toHaveBeenCalledWith(
        expect.objectContaining({ text: expect.stringContaining('proxies:') })
      )
    )
  })
})
