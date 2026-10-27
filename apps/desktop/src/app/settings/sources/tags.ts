/**
 * 设置 → 信源顶部那一排**标签筛选**（用户 10-27：「我偏向 1，然后用户可用标签筛选。挺好的」）。
 *
 * 轴只有一条：分组按「这条数据从哪来」（`registry.SOURCE_CATEGORIES`）。
 * 「这条数据是什么形状」不再占分类那一格——它的真源是**池里那条 `kind`**，
 * 界面上这一排就是从每行的 `pool_kinds` 里汇出来的，**不新建第二处真源**、不入库、
 * 也不给"用户自己打标签"留字段（那一格与待办 #32 是同一问，没拍）。
 *
 * 与折叠同一档持久化闸（本机记住、不进 `sources.yaml`）：它是**浏览决定**，
 * 不是一条要同步的事实——同一理由同一条口径。
 */

import { type Codec, persistentAtom } from '@/lib/persisted'
import { storageKey } from '@/lib/storage'

/** 只存认识的词：字符串数组，别的形状（别人塞进来的、存坏的）当作没记过。 */
const kinds: Codec<string[]> = {
  decode: raw => {
    const parsed = JSON.parse(raw) as unknown

    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  },
  // 空表不占一格：编成 `null` 让 storage 那道闸把键删掉（与折叠那张表同款）。
  encode: value => (value.length === 0 ? null : JSON.stringify(value))
}

export const $tags = persistentAtom<string[]>(storageKey('desktop.settings.sources.tags'), [], kinds)

/** 这一屏上真实出现过的形状，按条数从多到少（顺序稳定， chip 不会每刷一次换个位置）。 */
export function tagOptions(rows: Array<{ pool_kinds?: Record<string, number> }>): string[] {
  const totals = new Map<string, number>()

  for (const row of rows) {
    for (const [kind, count] of Object.entries(row.pool_kinds ?? {})) {
      totals.set(kind, (totals.get(kind) ?? 0) + Number(count || 0))
    }
  }

  return [...totals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(entry => entry[0])
}

/**
 * 一个都没选 = 没筛（全画）。选了几个 = 只画**池里有这些形状**的那些行。
 *
 * 一条从没采过的信源（`pool_kinds` 空）在任何筛选下都不出现——这是对的：
 * 筛的是"哪些形状的条目在池里"，一条空源没有条目可筛。
 */
export function keepsRow(row: { pool_kinds?: Record<string, number> }, selected: string[]): boolean {
  if (selected.length === 0) {
    return true
  }

  const present = Object.keys(row.pool_kinds ?? {})

  return present.some(kind => selected.includes(kind))
}

/** 点一枚 chip：在表里就摘掉，不在就加上（多选，与"只看这几类"那句话同形）。 */
export function toggleTag(selected: string[], kind: string): string[] {
  return selected.includes(kind) ? selected.filter(item => item !== kind) : [...selected, kind]
}
