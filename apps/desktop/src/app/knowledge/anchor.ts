/**
 * 锚点解析：把一个 wikilink 的 `#anchor` 变成页面上真实存在的那个元素。
 *
 * 盘上的笔记里锚点有两套，来源不同、都得认：
 *
 * 1. **HTML 锚点** —— Obsidian 导出的笔记在标题前手写了 `<a id="ch1"></a>`。
 *    例：`计算机/操作系统 Operating System/HarmonyOS.md` 的 11 条目录
 *    （`[[#ch1|1. 发展历程]]` …）全靠它跳转。**这些 id 不在标题上**，
 *    在标题前面的那个独立 `<a>` 元素上。
 * 2. **slug id** —— 我们自己给标题打的锚点（`parseOutline` 的 `h-` 前缀那套），
 *    对应 Obsidian 的 `[[#标题文字]]` 写法。
 *
 * 两套并存是事实，不是设计失误——所以查找顺序是：先找 `id` 完全相等的元素，
 * 再退到 slug 那套；都找不到才算「这篇里没有这个锚点」。
 */

import { slug as outlineSlug } from './outline'

export interface AnchorTarget {
  /** 元素的 DOM id（用来 scrollIntoView + 高亮）。 */
  id: string
  /** 锚点落在哪个标题上——HTML 锚点要往上找最近的标题才知道它是哪一节。 */
  headingId: string | null
}

/**
 * 在已渲染的容器里找一个锚点。
 *
 * `anchor` 是 wikilink 里 `#` 后面那段（可能是 `ch1`，也可能是标题文字）。
 */
export function findAnchor(container: HTMLElement, anchor: string): AnchorTarget | null {
  const key = anchor.trim()
  if (!key) return null

  // ① 精确 id：HTML 锚点（`id="ch1"`）与 slug id（`id="h-1-发展历程"`）都落在这。
  const direct = container.querySelector<HTMLElement>(`[id="${cssEscape(key)}"]`)
  if (direct) {
    return { id: direct.id, headingId: nearestHeadingId(container, direct) }
  }

  // ② slug 那套：Obsidian 的 `[[#标题文字]]` 是按标题文字找的，我们把标题文字
  //  规成 slug 再找——与 `parseOutline` 用的是同一个 slug 函数。
  const bySlug = container.querySelector<HTMLElement>(`[id="${cssEscape(`h-${outlineSlug(key)}`)}"]`)
  if (bySlug) {
    return { id: bySlug.id, headingId: bySlug.id }
  }

  return null
}

/**
 * 找离给定元素最近的标题 id。
 *
 * HTML 锚点 `<a id="ch1"></a>` 常常单独占一行、在标题**前面**，所以要往前找
 * （previousElementSibling 链），找不到再往后找。
 */
export function nearestHeadingId(container: HTMLElement, element: HTMLElement): string | null {
  let node: HTMLElement | null = element

  while (node && node !== container) {
    if (/^h[1-6]$/.test(node.tagName) && node.id) return node.id
    node = node.previousElementSibling as HTMLElement | null
  }

  node = element.nextElementSibling as HTMLElement | null
  while (node) {
    if (/^h[1-6]$/.test(node.tagName) && node.id) return node.id
    node = node.nextElementSibling as HTMLElement | null
  }

  return null
}

/** CSS 选择器里转义 id 里的特殊字符（标题文字可能带点、括号、空格）。 */
function cssEscape(value: string): string {
  const cssApi = (globalThis as { CSS?: { escape?: (v: string) => string } }).CSS
  if (cssApi?.escape) return cssApi.escape(value)
  return value.replace(/["\\]/g, '\\$&')
}
