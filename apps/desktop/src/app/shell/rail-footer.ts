import type { StatusbarItem } from './statusbar-controls'

/**
 * 左栏底下那一组只留六枚（用户 11-02 拍「6 枚」，此前 12 枚挤在一枚「⋯」里）。
 *
 * 判据两条：**有数要看**的留（网关·分身·日程·待确认），**没有别处入口**的留（出口·设置）；
 * 其余不是藏起来，是这一条不再重复一份入口——
 * 命令面板 ⌘. 与 ⌘K 里都有、Cron 在 ⌘K 里、快捷键面板 ⌘/、
 * 版本号与「检查更新」在设置·关于、触感静音搬进设置·通知（那一屏本来就管反馈），
 * 会话/回合时长是读数，只在展开态那条横排上显示，不进「⋯」那份清单。
 *
 * ⚠️ 那枚设置的 id 带 `system:` 前缀——它是 `desktop-controller` 把顶栏那三枚工具
 * （触感/快捷键/设置）映射成状态栏格子时加的。写裸 `settings` 不会报错，只会让这一格
 * 静悄悄少一枚（11-02 真浏览器里量出来就是少这一枚）。
 *
 * 挂在 `StatusbarControls` 的 `dock + inline` 那一支上（左栏底部专用），不挂在左栏调用方。
 */
export const RAIL_FOOTER_IDS: readonly string[] = [
  'gateway-health',
  'agents',
  'agenda',
  'mind-pending',
  'egress',
  'system:settings'
]

/** 按上面那张单子取，顺序以单子为准；没在这一刻存在的（hidden／还没挂上）就不占一格。 */
export function pickRailFooterItems(items: readonly StatusbarItem[]): StatusbarItem[] {
  return RAIL_FOOTER_IDS.map(id => items.find(item => item.id === id)).filter(
    (item): item is StatusbarItem => Boolean(item) && !item?.hidden
  )
}
