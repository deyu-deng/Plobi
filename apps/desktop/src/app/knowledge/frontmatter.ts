/**
 * 笔记的 frontmatter（属性面板要显示的那几格）。
 *
 * 为什么自己解析而不是引一个 YAML 库：渲染层只读**顶层那几格**，而且 Obsidian 的
 * 属性面板显示的就是「键 → 值」；引一个 YAML 解析器进来，为了看三个键不划算。
 *
 * 能认的形状（盘上笔记真在用的就这些）：
 *   `title: 微积分`                    → 一格字符串
 *   `belongs: []`                      → 一格空列表
 *   `tags: [数学, 极限]`               → 一格列表
 *   `relates:\n  - 微积分\n  - 线性代数` → 一格多行列表
 *
 * 认不出的（嵌套 map、块标量）**照原样留着那一行**，不假装解析成功。
 */

export interface FrontmatterEntry {
  key: string
  value: string
}

const KEY_LINE = /^([^\s:#][^:#]*?)\s*:\s*(.*)$/
const LIST_ITEM = /^\s*-\s+(.*)$/

/** 把一格的值收拾成能上屏的一行。 */
function normalizeValue(raw: string): string {
  const trimmed = raw.trim()

  if (!trimmed.startsWith('[')) return trimmed.replace(/^['"]|['"]$/g, '')

  return trimmed
    .replace(/^\[/, '')
    .replace(/\]$/, '')
    .split(',')
    .map(item => item.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean)
    .join('、')
}

/** 解析一篇笔记的 frontmatter；没有就返回空数组。 */
export function parseFrontmatter(text: string): FrontmatterEntry[] {
  if (!text.startsWith('---')) return []

  const end = text.indexOf('\n---', 3)

  if (end < 0) return []

  const block = text.slice(3, end)
  const out: FrontmatterEntry[] = []
  let current: FrontmatterEntry | null = null

  for (const line of block.split(/\r?\n/)) {
    if (!line.trim()) continue

    const item = LIST_ITEM.exec(line)

    if (item && current) {
      const value = item[1].trim()

      current.value = current.value ? `${current.value}、${value}` : value
      continue
    }

    const pair = KEY_LINE.exec(line)

    if (!pair) continue

    current = { key: pair[1].trim(), value: normalizeValue(pair[2] ?? '') }
    out.push(current)
  }

  return out
}
