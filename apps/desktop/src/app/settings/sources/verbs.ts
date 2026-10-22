/**
 * 这一行的状态与开关 —— **判据搬去后端了，这里只画**。
 *
 * 10-22 之前这里是七条规则算出的七种胶囊话（`在采 / 没采过 / 停了 / 读不到库 / 坏了 / 关着 / 不知道`），
 * 加上三个动词（`试一次 / 打开 / 停掉`）。用户对着那一屏说的原话是
 * 「**一个信源就一个开关按钮，就三种形态：未连接和连接，然后出错了会有一个报错。不要有这么多按钮**」。
 *
 * 为什么不是把七条压成三条继续留在前端：**同一件事不该有两处判据**。后端已经有全部输入
 * （开关生效值、声明里缺哪格、台账里上一轮的状态与那句话、活性、池里的条数），
 * 它算完给 `status` / `status_code` / `status_detail`；前端再算一遍就会出现
 * 「胶囊说开着、按钮说试一次」那种自相矛盾的一行——而这次连矛盾的第二份真源都没留。
 *
 * 所以这一档现在只剩四件纯展示辅助：把状态读成一个词、把开关读成一个布尔、
 * "这一条还缺必填吗"（它决定第一次点开关是去配置还是真开）、以及把后端那个原因码查成
 * 目录里的那句人话。
 */

import type { SourceRow } from './api'

/** 三态的闭集。后端给了别的词就落 `disconnected` —— **一个没见过的词不许画成绿点**。 */
export type SourceStatus = 'connected' | 'disconnected' | 'error'

const STATUSES: readonly SourceStatus[] = ['connected', 'disconnected', 'error']

export function statusOf(row: SourceRow): SourceStatus {
  return row.status && STATUSES.includes(row.status) ? row.status : 'disconnected'
}

/**
 * 开关画在哪一边。`null` 是**读数**：配置层没读到，不等于"没点"。
 *
 * 把"读不到"画成一个关着的开关，就是下一颗假绿——用户会以为是自己手滑关的。
 */
export function switchOn(row: SourceRow): null | boolean {
  return row.enabled === undefined ? null : row.enabled
}

/**
 * 这一条还缺必填吗。**只回 true/false，不回缺哪一格**——缺哪一格是表单自己的事
 * （它就在抽屉里，一眼看得见），而"这一条还没配"是行级的一个标记。
 *
 * 密钥那一格看 `secrets`（值永不出现在读数里，所以"填没填"只能问后端那一格设过没有）。
 */
export function needsSetup(row: SourceRow): boolean {
  if (row.setup_fields.length === 0) {
    return false
  }

  return row.setup_fields.some(field => {
    if (!field.required) {
      return false
    }

    if (field.kind === 'secret') {
      return row.secrets?.[field.key] !== true
    }

    return !String(row.setup?.[field.key] ?? '').trim()
  })
}

/**
 * 那一格的原因怎么说 —— **查语言目录，不自己造句**。
 *
 * 用户 10-22 拍的是：行上只要一句人话，后端那句逐字原话进抽屉（"出错会有一个报错"，
 * 但「注册表说这条在采，但没有任何一方构造得出它的采集器」那种话不该出现在设置页上）。
 * 后端给的是 `status_code`（裁定 45.4：码是给界面查目录用的，**不许上屏**），
 * 这里只做一次查表：查到就用四语里的那一句，查不到落 `reasonUnknown`（「暂未适配」）。
 *
 * 判据仍然只有一处：`plobi/collectors/sources_api.py` 的 `status_of`。这一档**不认识任何状态词**，
 * 它只认识"目录里有没有这一码"——所以新增一个采集器状态词不会在这里长出第二套判据，
 * 只会退那一兜底句。
 */
export function statusReason(row: SourceRow, labels: { reasonUnknown: string, reasons: Record<string, string> }): string {
  const code = row.status_code ?? ''

  return (code ? labels.reasons[code] : undefined) ?? labels.reasonUnknown
}

/** 按分类分组。**空分类不出现** —— 见 `visibleGroups` 的理由。 */export function groupByCategory(
  rows: SourceRow[],
  categories: { id: string; sources: string[] }[]
): { category: string; rows: SourceRow[] }[] {
  const byId = new Map(rows.map(row => [row.source, row]))

  return categories.map(group => ({
    category: group.id,
    rows: group.sources.map(id => byId.get(id)).filter((row): row is SourceRow => Boolean(row))
  }))
}

/**
 * 真正要渲染的那几类。
 *
 * **空的分类整块不画** —— 用户 10-19 原话「很多东西都是没必要展示给用户的信息」。
 * `custom` 是唯一的例外，而且不是因为它有成员，是因为**它那一格里有一个动作**
 * （装一个自己的，见 §2.2：「其他」不当分类，它就是那个逃生口）。
 */
export function visibleGroups<T extends { category: string; rows: unknown[] }>(
  groups: T[],
  alwaysShow: readonly string[] = ['custom']
): T[] {
  return groups.filter(group => group.rows.length > 0 || alwaysShow.includes(group.category))
}
