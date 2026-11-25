import { DEFAULT_SLOT_DECLARATION, type SlotDeclaration } from './slot-registry'
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
 * （出口·设置）；其余不是藏起来，是这一条不再重复一份入口——快捷键面板 ⌘/、
 * 版本号与「检查更新」在设置·关于、触感静音搬进设置·通知（那一屏本来就管反馈），
 * 会话/回合时长是读数，只在展开态那条横排上显示，不进「⋯」那份清单。
 *
 * ⚠️ **命令中心现在也在这一族里**（用户 11-25 22:2x 拍定：「日程和命令中心属于那个
 * 地方的东西」）——11-03 那条「命令面板 ⌘. 与 ⌘K 里都有，不重复一份入口」**被这一句
 * 覆盖**：屏上左栏底部**多一枚命令中心图标**（⌘. 仍然能用）。
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
/**
 * 这一列**从 slot 声明表派生**（`slot-registry.ts` 的 `sidebar`／`bottom` 区）——那里是唯一一份。
 *
 * ⚠️ **它是另一种 slot 家族**（用户 11-25 22:2x：「我们侧边栏底部的图标是**另一种
 * slot**」）：**`status-item`**（状态栏那一件）——形状与上面那排一样（一枚小图标按钮，
 * 用户 11-25 12:2x「上面和下面其实差不多」），**宿主管的东西不同**：内容来自状态栏
 * 管道、点开是整屏或一小张面板。
 *
 * **顺序是这一档的全部判据**（用户 11-03 拍：「Overlay 展示的组件放在前面，其他的放在后面，
 * 这也算整齐了。设置放在最前面」）：点开就是一整屏的排前面，点开只有一小张面板（弹窗）的排后面。
 * **这一条现在是声明里的一个字段**（`props.opens`：`'overlay'` ／ `'panel'`）⇒ 判据是
 * **数据**，不是测试里那份写死的清单。
 *
 * ⚠️ 写脑待批那一枚**不在这一列**（`WP-MIND-CONSENT-EVENTS`）：用户 11-16 21:4x 拍定「放在左侧栏底部的
 * 一个图标里面…我打算删掉这个图标」⇒ 入口搬进对话卡抬头（`app/shell/chat-top-bar.tsx`），
 * 与票面待批并排。要点头的东西在说话那张卡上。
 */
export const RAIL_FOOTER_IDS: readonly string[] = DEFAULT_SLOT_DECLARATION.filter(
  slot => slot.position === 'sidebar' && slot.kind === 'status-item'
)
  .sort((a, b) => a.order - b.order)
  .map(slot => slot.id)

/** 那一列的声明（顺序照声明表）——要问「哪几枚点开是整屏」时看它。 */
export function railFooterSlots(): readonly SlotDeclaration[] {
  return DEFAULT_SLOT_DECLARATION.filter(slot => slot.position === 'sidebar' && slot.kind === 'status-item').sort(
    (a, b) => a.order - b.order
  )
}

/** 按上面那张单子取，顺序以单子为准；没在这一刻存在的（hidden／还没挂上）就不占一格。 */
export function pickRailFooterItems(items: readonly StatusbarItem[]): StatusbarItem[] {
  return RAIL_FOOTER_IDS.map(id => items.find(item => item.id === id)).filter(
    (item): item is StatusbarItem => Boolean(item) && !item?.hidden
  )
}
