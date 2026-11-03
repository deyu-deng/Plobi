import { describe, expect, it, vi } from 'vitest'

import type { OverlayNavGroup } from '../overlays/overlay-split-layout'

import { filterNavGroups } from './index'

/**
 * 设置导航顶上那格过滤（Qoder 那一屏就有这一格）。这一档钉那两条判据：
 * 命中组名 ⇒ 整组上不带子项；只命中子项 ⇒ 把那条提到顶层——
 * 藏在"要点开那一组才看得见"的位置，搜到了却点不到，等于没搜。
 */
const link = (label: string): OverlayNavGroup => ({
  active: false,
  icon: (() => null) as unknown as OverlayNavGroup['icon'],
  id: label,
  label,
  onSelect: vi.fn()
})

const GROUPS: OverlayNavGroup[] = [
  link('外观'),
  { ...link('模型'), children: [link('默认模型'), link('思考强度')] },
  { ...link('提供方'), children: [link('账号'), link('API 密钥')] }
]

describe('设置导航的过滤', () => {
  it('空查询原样返回，不吞掉任何一组', () => {
    expect(filterNavGroups(GROUPS, '   ').map(group => group.label)).toEqual(['外观', '模型', '提供方'])
  })

  it('命中组名：整组上，不带子项', () => {
    const out = filterNavGroups(GROUPS, '提供')

    expect(out.map(group => group.label)).toEqual(['提供方'])
    expect(out[0]?.children).toBeUndefined()
  })

  it('只命中子项：把那一条提到顶层，仍然点得动', () => {
    const out = filterNavGroups(GROUPS, '密钥')

    expect(out.map(group => group.label)).toEqual(['API 密钥'])
    expect(out[0]?.onSelect).toBeTypeOf('function')
  })

  it('什么都命中不到就是空，不留半截组', () => {
    expect(filterNavGroups(GROUPS, '不存在的东西')).toEqual([])
  })
})
