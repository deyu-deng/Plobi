import { describe, expect, it } from 'vitest'

import { pickRailFooterItems, RAIL_FOOTER_IDS } from './rail-footer'
import type { StatusbarItem } from './statusbar-controls'

/**
 * 左栏底部那一组只留六枚（用户 11-02 拍）。这一档钉两件事：
 * ① 单子就是单子——顺序照 `RAIL_FOOTER_IDS`，状态栏那边怎么排的不算；
 * ② 掉出去的那几枚是**不再在这条上重复入口**，不是被谁顺手藏了：读数（会话/回合时长、
 *    版本号）与已经另有入口的（命令面板、Cron、快捷键、触感）都不许自己又挤回来。
 */

const item = (id: string, extra: Partial<StatusbarItem> = {}): StatusbarItem => ({ id, ...extra })

const EVERY_TODAY = [
  item('haptics'),
  item('keybinds'),
  item('system:settings'),
  item('command-center'),
  item('gateway-health'),
  item('agents'),
  item('cron'),
  item('agenda'),
  item('mind-pending'),
  item('egress'),
  item('running-timer'),
  item('session-timer'),
  item('version-client'),
  item('version-backend')
]

describe('左栏底部那一组', () => {
  it('正好六枚，顺序照单子', () => {
    expect(RAIL_FOOTER_IDS).toEqual(['gateway-health', 'agents', 'agenda', 'mind-pending', 'egress', 'system:settings'])
    expect(pickRailFooterItems(EVERY_TODAY).map(i => i.id)).toEqual(RAIL_FOOTER_IDS)
  })

  it('读数与另有入口的那几枚不许自己挤回来', () => {
    const ids = pickRailFooterItems(EVERY_TODAY).map(i => i.id)

    for (const dropped of [
      'command-center',
      'cron',
      'haptics',
      'keybinds',
      'running-timer',
      'session-timer',
      'version-backend',
      'version-client'
    ]) {
      expect(ids, `${dropped} 又回到底部这一组了`).not.toContain(dropped)
    }
  })

  it('没挂上或该藏的那一格不占位', () => {
    const withoutEgress = EVERY_TODAY.filter(i => i.id !== 'egress')

    expect(pickRailFooterItems(withoutEgress).map(i => i.id)).toEqual([
      'gateway-health',
      'agents',
      'agenda',
      'mind-pending',
      'system:settings'
    ])

    const gatewayHidden = EVERY_TODAY.map(i => (i.id === 'gateway-health' ? item(i.id, { hidden: true }) : i))

    expect(pickRailFooterItems(gatewayHidden).map(i => i.id)).not.toContain('gateway-health')
  })
})
