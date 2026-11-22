import { describe, expect, it } from 'vitest'

import {
  asEgressBlock,
  type EgressBlock,
  exitOf,
  isCatchAll,
  modeOf,
  patchExit,
  patchMode,
  patchRuleAdd,
  patchRuleCatchAll,
  patchRuleMove,
  patchRuleRemove,
  patchRuleVia,
  patchTransport,
  proxyTooltip,
  readRule,
  transportOf
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

//: 句子来自 i18n 目录，所以这里比的是「哪一句被选中」，不是措辞。
const COPY = {
  proxyDeclaredNotApplied: 'declared-but-not-ours',
  proxyNotOurs: 'not-taken-over',
  proxyOurs: 'we-hold-it',
  proxyUnsupported: 'windows-only'
}

describe('proxyTooltip：声明与注册表是两句话', () => {
  it('四种读数各选各的那一句', () => {
    expect(proxyTooltip(COPY, { declared: true, ours: true, supported: true })).toBe('we-hold-it')
    // 这台 10-31 的真实形状：表说要接管，别的程序把注册表改回去了。选"在管"那一句就是假话。
    expect(proxyTooltip(COPY, { declared: true, ours: false, supported: true })).toBe('declared-but-not-ours')
    expect(proxyTooltip(COPY, { declared: false, ours: false, supported: true })).toBe('not-taken-over')
    expect(proxyTooltip(COPY, { declared: true, ours: true, supported: false })).toBe('windows-only')
  })

  it('没有读数就不说话——把"读不到"报成"没接管"是另一种谎', () => {
    expect(proxyTooltip(COPY, undefined)).toBe('')
  })
})

describe('规则行：读得出来、删得掉、挪不过兜底', () => {
  it('认得出引擎认的每一类，也认得出兜底那条', () => {
    // 这张表与 `agent/egress/rules.py` 的 `_KIND_PREFIX` 同源：屏幕认不出的一行，
    // 到了引擎那里就是另一个意思，两套认法是必须避免的那种 bug。
    expect(readRule('example.com').kind).toBe('domain')
    expect(readRule('*.example.com').kind).toBe('suffix')
    expect(readRule('1.2.3.0/24').kind).toBe('cidr')
    expect(readRule('geosite:cn').kind).toBe('geosite')
    expect(readRule('geoip:cn').kind).toBe('geoip')
    expect(readRule('keyword:ads').kind).toBe('keyword')
    expect(readRule('regex:^ad\\.').kind).toBe('regex')
    expect(readRule('process:C:\\a b.exe').kind).toBe('process')
    expect(readRule('src:192.168.0.0/16').kind).toBe('src')
    expect(readRule('*').kind).toBe('catchAll')
  })

  it('写不出来的那一行当场说错在哪，而不是当成一条奇怪的域名收下', () => {
    expect(readRule('').error).toBe('empty')
    expect(readRule('geosite:').error).toBe('emptyPayload')
    // 冒号前不是已知前缀：`example.com:8080` 也走这里 —— 规则里端口没有意义。
    expect(readRule('foo:bar').error).toBe('badPrefix')
    expect(readRule('a b.com').error).toBe('space')
    // 夹在中间的通配引擎不认；只认开头的 `*.`（子域名）与单独一条 `*`（兜底）。
    expect(readRule('api.*.com').error).toBe('wildcard')
    // 这两个前缀的取值里带空格是正常的，别误判。
    expect(readRule('regex:^ad guard$').error).toBeUndefined()
    expect(readRule('process:C:\\Program Files\\a.exe').error).toBeUndefined()
  })

  it('加一条插在兜底**之前**——插后面等于没插', () => {
    const next = patchRuleAdd(block(), '*.example.com', 'proxy:a')

    expect(next.rules?.map(rule => rule.match)).toEqual(['geosite:cn', '*.example.com', '*'])
    expect(next.rules?.[1].via).toBe('proxy:a')
  })

  it('同一个匹配再加一次就是改它的去向，不留一条永远轮不到的死人', () => {
    const next = patchRuleAdd(block(), 'geosite:cn', 'proxy:a')

    expect(next.rules).toHaveLength(2)
    expect(next.rules?.[0]).toEqual({ match: 'geosite:cn', via: 'proxy:a' })
  })

  it('兜底那条删不得：删了它，"没匹配上的去哪"就没有答案', () => {
    const rules = block().rules ?? []
    const at = rules.findIndex(isCatchAll)

    expect(() => patchRuleRemove(block(), at)).toThrow()
    // 别的规则照删。
    expect(patchRuleRemove(block(), 0).rules?.map(rule => rule.match)).toEqual(['*'])
  })

  it('兜底那条也挪不动，别的规则跨不过它', () => {
    // 已经贴着兜底的那一条再往下：原地还回同一个块（屏幕据此什么都不发）。
    const same = block()
    expect(patchRuleMove(same, 0, 1)).toBe(same)
    // 兜底自己也不动。
    expect(() => patchRuleMove(same, 1, -1)).not.toThrow()
    expect(patchRuleMove(same, 1, -1)).toBe(same)
  })

  it('改去向只动那一条；补兜底走的是同一条加规则的路', () => {
    const via = patchRuleVia(block(), 0, 'proxy:a')
    expect(via.rules?.[0].via).toBe('proxy:a')
    expect(via.rules?.[1]).toEqual({ match: '*', via: 'proxy:POOL' })

    const added = patchRuleCatchAll(block({ rules: [{ match: 'geosite:cn', via: 'direct' }] }), 'direct')
    expect(added.rules?.map(rule => rule.match)).toEqual(['geosite:cn', '*'])
  })
})
