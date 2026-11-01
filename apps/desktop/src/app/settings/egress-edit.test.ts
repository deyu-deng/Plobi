import { describe, expect, it } from 'vitest'

import {
  asEgressBlock,
  exitOf,
  modeOf,
  patchExit,
  patchMode,
  patchTransport,
  proxyTooltip,
  transportOf,
  type EgressBlock
} from './egress-edit'

const TUN_BLOCK = { 'allow-foreign': false, name: 'PlobiTun', stack: 'gvisor' }

const block = (over: Partial<EgressBlock> = {}): EgressBlock =>
  asEgressBlock({
    engine: { enabled: true, mixed_port: 7895 },
    groups: { POOL: { include: ['a'], type: 'url-test' } },
    nodes: { a: { port: 1, server: 'x.example', type: 'ss' } },
    rules: [
      { match: 'geosite:cn', via: 'direct' },
      { match: '*', via: 'proxy:POOL' }
    ],
    system_proxy: { enabled: true },
    ...over
  } as EgressBlock)

describe('换载体是模式而非叠加', () => {
  it('拨到 TUN 就把系统代理关掉，并把守卫给的网卡形状原样带上', () => {
    const next = patchTransport(block(), 'tun', TUN_BLOCK)

    expect(next.engine?.tun).toEqual({ ...TUN_BLOCK, enabled: true })
    expect(next.system_proxy).toEqual({ enabled: false })
    expect(transportOf(next)).toBe('tun')
  })

  it('拨回系统代理时 TUN 的 shape 留着、只是 enabled 假——拨来拨去不许把人配好的网卡抹掉', () => {
    const on = patchTransport(block(), 'tun', TUN_BLOCK)
    const back = patchTransport(on, 'system')

    expect(back.engine?.tun).toEqual({ ...TUN_BLOCK, enabled: false })
    expect(back.system_proxy).toEqual({ enabled: true })
    expect(transportOf(back)).toBe('system')
  })

  it('关掉是两半都关：表里没有声明、注册表那一半由面板去交还', () => {
    const off = patchTransport(block(), 'off', TUN_BLOCK)

    expect(off.system_proxy).toEqual({ enabled: false })
    expect(off.engine?.tun?.enabled).toBe(false)
    expect(transportOf(off)).toBe('off')
  })
})

describe('规则/全局与默认出口', () => {
  it('全局只摘那条分类直连，兜底规则还在最后', () => {
    const global = patchMode(block(), 'global')

    expect(global.rules).toEqual([{ match: '*', via: 'proxy:POOL' }])
    expect(modeOf(global)).toBe('global')
  })

  it('按规则把分类直连插回兜底**之前**——插后面等于没插', () => {
    const back = patchMode(patchMode(block(), 'global'), 'rule')

    expect(back.rules?.map(r => r.match)).toEqual(['geosite:cn', '*'])
    expect(modeOf(back)).toBe('rule')
  })

  it('选节点只动兜底规则，其余一条不碰', () => {
    const withExtra = block({
      rules: [
        { match: 'geosite:cn', via: 'direct' },
        { match: 'store.steampowered.com', via: 'direct' },
        { match: '*', via: 'proxy:POOL' }
      ]
    })
    const next = patchExit(withExtra, 'a')

    expect(next.rules?.[0]).toEqual({ match: 'geosite:cn', via: 'direct' })
    expect(next.rules?.[1]).toEqual({ match: 'store.steampowered.com', via: 'direct' })
    expect(next.rules?.[2]).toEqual({ match: '*', via: 'proxy:a' })
    expect(exitOf(next)).toBe('a')
  })

  it('表里还没有兜底规则就补一条：默认出口必须意味着"没匹配上的都去哪"', () => {
    const none = block({ rules: [{ match: 'geosite:cn', via: 'direct' }] })
    const next = patchExit(none, 'POOL')

    expect(next.rules).toEqual([
      { match: 'geosite:cn', via: 'direct' },
      { match: '*', via: 'proxy:POOL' }
    ])
  })

  it('空名字是错，不是"把代理前缀写空"', () => {
    expect(() => patchExit(block(), '  ')).toThrowError()
  })
})

describe('proxyTooltip：声明与注册表是两句话', () => {
  it('四种读数各说各的', () => {
    expect(proxyTooltip({ declared: true, ours: true, supported: true })).toBe('Plobi 在管这台机器的系统代理')
    // 这台 10-31 的真实形状：表说要接管，别的程序把注册表改回去了。说"在管"就是那句假话。
    expect(proxyTooltip({ declared: true, ours: false, supported: true })).toBe('表里说要接管，但注册表里不是我们的')
    expect(proxyTooltip({ declared: false, ours: false, supported: true })).toBe('不是 Plobi 的（跟着系统或别的程序）')
    expect(proxyTooltip({ declared: true, ours: true, supported: false })).toBe('这一格目前只实现 Windows')
  })

  it('没有读数就不说话——把"读不到"报成"没接管"是另一种谎', () => {
    expect(proxyTooltip(undefined)).toBe('')
  })
})
