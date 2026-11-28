import { describe, expect, it } from 'vitest'

import { pickRailFooterItems, RAIL_FOOTER_IDS, railFooterSlots } from './rail-footer'
import type { StatusbarItem } from './statusbar-controls'

/**
 * 左栏底部那一组（用户 11-03 拍顺序；**11-27 13:0x 又分了一次家**）。
 *
 * **用户 11-25 22:4x 起的口径**：「**下面的**一般是**控制目前显示的这个 Agent 的组件**」、
 * 「**上面的消息平台和知识库是一般是全局的**」⇒ 那一排＝**这一摊的（代理）＋
 * 整机的（网关／出口）**；**全局的那几条**（设置·命令中心·排程·日程）**上到上面那排**了。
 * 整机的两条**不挪到窗口那条状态栏**：窗口那条状态栏**已经退役了**（用户 11-28 00:1x
 * 删掉了没有卡的那副布局 ⇒ 那条状态栏**整条没了**）⇒ 那些读数（会话/回合时长）**搬进
 * 输入框下面那条上下文条的左边**（`composer/context-footer.tsx`）。
 *
 * 这一档钉三件事：
 * ① 单子就是单子——顺序照 `RAIL_FOOTER_IDS`，状态栏那边怎么排的不算；
 * ② 每一格都带着**作用域**（这一摊的／整机的）；
 * ③ 没挂上或该藏的那一格不占位。
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
  it('那一排＝**这一摊的 ＋ 整机的**（全局的那几条上到上面那排去了）', () => {
    expect(RAIL_FOOTER_IDS).toEqual(['agents', 'gateway-health', 'egress'])
    expect(pickRailFooterItems(EVERY_TODAY).map(i => i.id)).toEqual(RAIL_FOOTER_IDS)
  })

  it('每一格都带着**作用域**（用户 11-25 22:4x：下面的是控制当前这个 Agent 的组件）', () => {
    const scopes = new Map(railFooterSlots().map(slot => [slot.id, slot.scope]))

    // 这一摊的
    expect(scopes.get('agents')).toBe('agent')
    // 整机的（默认 global ⇒ 不标也成）
    expect(scopes.get('gateway-health')).toBeUndefined()
    expect(scopes.get('egress')).toBeUndefined()
  })

  it('全局的那几条**不在这一排**了（它们在上面那排）', () => {
    const ids = pickRailFooterItems(EVERY_TODAY).map(i => i.id)

    for (const moved of ['system:settings', 'command-center', 'cron', 'agenda']) {
      expect(ids, `${moved} 还留在左栏这一排`).not.toContain(moved)
    }
  })

  it('没挂上或该藏的那一格不占位', () => {
    const withoutEgress = EVERY_TODAY.filter(i => i.id !== 'egress')

    expect(pickRailFooterItems(withoutEgress).map(i => i.id)).toEqual(['agents', 'gateway-health'])

    const gatewayHidden = EVERY_TODAY.map(i => (i.id === 'gateway-health' ? item(i.id, { hidden: true }) : i))

    expect(pickRailFooterItems(gatewayHidden).map(i => i.id)).not.toContain('gateway-health')
  })
})
