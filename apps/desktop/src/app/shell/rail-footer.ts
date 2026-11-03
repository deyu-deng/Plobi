import type { StatusbarItem } from './statusbar-controls'

/**
 * 左栏底下那一组（此前 12 枚挤在一枚「⋯」里）。
 *
 * **顺序是这一档的全部判据**（用户 11-03 拍：「Overlay 展示的组件放在前面，其他的放在
 * 后面，这也算整齐了。设置放在最前面」）：点开就是一整屏的排前面，点开只有一小张面板
 * （弹窗）的排后面。两类各自内部照使用频次，不再掺在一起——之前 11-02 只拍了"留几枚"，
 * 没管顺序，于是「⋯」那份清单第一行是网关、最后一行是设置，读起来没有规律。
 *
 * 留哪几枚的判据两条：**有数要看**的留（分身·日程·待确认·网关），**没有别处入口**的留
 * （出口·设置）；其余不是藏起来，是这一条不再重复一份入口——
 * 命令面板 ⌘. 与 ⌘K 里都有、快捷键面板 ⌘/、
 * 版本号与「检查更新」在设置·关于、触感静音搬进设置·通知（那一屏本来就管反馈），
 * 会话/回合时长是读数，只在展开态那条横排上显示，不进「⋯」那份清单。
 *
 * ⚠️ Cron 11-03 明确要回来（「Cron 一定要先回来」）。此前把它划走的理由"⌘K 里有"不算
 * 数——那是搜索入口，不是一个常驻的格子。
 *
 * ⚠️ 那枚设置的 id 带 `system:` 前缀——它是 `desktop-controller` 把顶栏那三枚工具
 * （触感/快捷键/设置）映射成状态栏格子时加的。写裸 `settings` 不会报错，只会让这一格
 * 静悄悄少一枚（11-02 真浏览器里量出来就是少这一枚）。
 *
 * 挂在 `StatusbarControls` 的 `dock + inline` 那一支上（左栏底部专用），不挂在左栏调用方。
 */
export const RAIL_FOOTER_IDS: readonly string[] = [
  // 点开是一整屏的
  'system:settings',
  'agents',
  'cron',
  'agenda',
  'mind-pending',
  // 点开是一小张面板的
  'gateway-health',
  'egress'
]

/** 按上面那张单子取，顺序以单子为准；没在这一刻存在的（hidden／还没挂上）就不占一格。 */
export function pickRailFooterItems(items: readonly StatusbarItem[]): StatusbarItem[] {
  return RAIL_FOOTER_IDS.map(id => items.find(item => item.id === id)).filter(
    (item): item is StatusbarItem => Boolean(item) && !item?.hidden
  )
}
