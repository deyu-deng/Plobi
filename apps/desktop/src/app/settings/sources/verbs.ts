/**
 * 动词表 —— `Docs/specs/source-layer.md` §6.1（用户 10-19 逐条拍定）的那一张，**逐字搬过来**。
 *
 * 为什么它是一张纯函数而不是一段组件里的 `if`：
 *
 * 1. **次序是判据的一部分。** 开关没点时"从来没采过"不是新闻（本来就没在跑），先报它
 *    等于把"没点开关"说成"坏了"。而 `has_builder === false` 排在开关**前面**，因为给它
 *    一个「打开」是骗人——开了也不会有东西进来。把次序藏进 `if` 的缩进里，下一个读的人
 *    看不见，就一定会改错。
 * 2. **P6 那条约束在这里是可测的。** "面上不许出现按了没用的按钮"如果只活在 JSX 里，
 *    没有任何东西能钉住它；做成一张表之后，"每一行都映射到一个存在的动词（或明确
 *    没有）"是可以枚举的。
 *
 * 用户 10-19 第二轮追加：界面上**只留一个状态胶囊**，其余（后端那句工程说明、时刻、
 * "为什么不猜"那一段）**都不上屏**——它们进日志与通知。所以这张表同时吐出
 * `pillFor`（状态）与 `verbFor`（动作），两者**永远从同一条规则来**：分开算就会出现
 * 「胶囊说开着、按钮说试一次」那种自相矛盾的一行。
 */

import type { SourceRow } from './api'

/** 这一行配哪一格。下标就是 §6.1 的次序，**改了次序先改规格**。 */
export const VERB_RULES = [
  {
    // A —— 产品缺陷不是设置。面只报，不给动词。
    id: 'noBuilder',
    matches: (row: SourceRow) => row.enabled === true && row.has_builder === false,
    verb: null
  },
  {
    // B —— 开关没点。注意这里是 `enabled === false`，**不是** `!enabled`：读不到开关
    //     （`null`）不等于没点，把"读不到"画成一个"关着"的开关就是一颗假绿。
    id: 'switchOff',
    matches: (row: SourceRow) => row.enabled === false,
    verb: 'enable'
  },
  {
    // C/D —— 从没成功过 / 停了。两者同一个动词：「试一次」。
    id: 'switchOnNotLive',
    matches: (row: SourceRow) => row.enabled === true && (row.state === 'never' || row.state === 'stale'),
    verb: 'try'
  },
  {
    // F —— 读不到日程库。把"读不到"说成"没采"就是下一颗假绿。
    id: 'noStore',
    matches: (row: SourceRow) => row.state === 'no_store',
    verb: null
  },
  {
    // E —— 还在进。
    id: 'live',
    matches: (row: SourceRow) => row.enabled === true && row.state === 'live',
    verb: 'disable'
  },
  {
    // 开关读不到 / 状态没记到。不猜是哪一种。
    id: 'unknown',
    matches: () => true,
    verb: null
  }
] as const

export type SourceVerb = 'disable' | 'enable' | 'try'
export type SourceRule = (typeof VERB_RULES)[number]['id']

/** 状态胶囊的语言目录键。与 `VERB_RULES` 一一对应，**不许**在这里另起一套。 */
export type SourcePillKey =
  | 'noBuilder'
  | 'pillLive'
  | 'pillNoStore'
  | 'pillNotStarted'
  | 'pillStale'
  | 'pillSwitchOff'
  | 'pillUnknown'

function ruleOf(row: SourceRow): (typeof VERB_RULES)[number] {
  for (const rule of VERB_RULES) {
    if (rule.matches(row)) {
      return rule
    }
  }

  return VERB_RULES[VERB_RULES.length - 1]
}

export function ruleFor(row: SourceRow): SourceRule {
  return ruleOf(row).id
}

export function verbFor(row: SourceRow): null | SourceVerb {
  return ruleOf(row).verb
}

export function pillFor(row: SourceRow): SourcePillKey {
  switch (ruleOf(row).id) {
    case 'live':
      return 'pillLive'

    case 'switchOff':
      return 'pillSwitchOff'

    case 'switchOnNotLive':
      return row.state === 'never' ? 'pillNotStarted' : 'pillStale'

    case 'noStore':
      return 'pillNoStore'

    case 'noBuilder':
      return 'noBuilder'

    default:
      return 'pillUnknown'
  }
}

/** 只有「还在进」那一个是"好"的状态。别的都 muted —— 不给它们颜色等于在说"有话要说"。 */
export function pillTone(row: SourceRow): 'muted' | 'primary' {
  return ruleOf(row).id === 'live' ? 'primary' : 'muted'
}

/**
 * 这一条还缺首次配置吗。**只回 true/false，不回缺哪一格** —— 缺哪一格是表单自己的
 * 事（它就在界面上，一眼看得见），而"这一条还没配"是行级的一个标记。
 */
export function needsSetup(row: SourceRow): boolean {
  if (row.setup_fields.length === 0) {
    return false
  }

  return row.setup_fields.some(field => field.required && !String(row.setup?.[field.key] ?? '').trim())
}

/** 按分类分组。**空分类不出现** —— 见 `visibleGroups` 的理由。 */
export function groupByCategory(
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
 * 三个「这一类还没接任何信源」占掉半屏，而那不是信息，是分类表自己的形状。
 *
 * `custom` 是唯一的例外，而且不是因为它有成员，是因为**它那一格里有一个动作**
 * （装一个自己的，见 §2.2：「其他」不当分类，它就是那个逃生口）。
 */
export function visibleGroups<T extends { category: string; rows: unknown[] }>(
  groups: T[],
  alwaysShow: readonly string[] = ['custom']
): T[] {
  return groups.filter(group => group.rows.length > 0 || alwaysShow.includes(group.category))
}