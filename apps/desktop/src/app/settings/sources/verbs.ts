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
 * 表里的每一格都**只读后端给的那几个字段**。这一层不许根据 `state` 造新句子——不许
 * 描述不存在的机制，也不许把"读不到"说成"没采"。
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
export type SourceVerbRule = (typeof VERB_RULES)[number]['id']

export function verbFor(row: SourceRow): null | SourceVerb {
  for (const rule of VERB_RULES) {
    if (rule.matches(row)) {
      return rule.verb
    }
  }

  return null
}

export function ruleFor(row: SourceRow): SourceVerbRule {
  for (const rule of VERB_RULES) {
    if (rule.matches(row)) {
      return rule.id
    }
  }

  return 'unknown'
}

/** 按分类分组，空分类也出现 ——「这一类还没接任何信源」也是一句话，不该由界面自己编。 */
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