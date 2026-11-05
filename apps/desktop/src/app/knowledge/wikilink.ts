/**
 * Obsidian 风格的 `[[wikilink]]` 解析。
 *
 * 盘上的笔记里真的有三种形态（2026-11-05 现核，49 处 / 10 篇）：
 *
 * 1. **跨文件** `[[CATIA]]` `[[具身认知]]` —— 指向另一篇笔记
 * 2. **同文件标题** `[[#ch1|1. 发展历程]]` —— 指向本篇的一个锚点。
 *    ⚠️ 这批锚点是 Obsidian 导出的 `<a id="ch1"></a>` HTML 锚点，**不是**我们
 *    `parseOutline` 生成的 slug id。所以跳转时 HTML 锚点与 slug id 两套都得认。
 * 3. **嵌入** `![[image.png|300]]` —— 带 `!` 前缀，是插图不是链接。
 *
 * 解析不出来的东西（目标文件不存在）**不静默**：解析器照样吐出结构，由渲染层
 * 决定怎么显示（灰色未创建样式）。Obsidian 也是这么做的——链接指向不存在的
 * 文件时显示成一个待创建的条目，而不是普通文字。
 */

export type WikiLinkKind = 'note' | 'anchor' | 'embed'

export interface WikiLink {
  /** 去掉 `[[ ]]`、去掉 `|` 别名、去掉 `#锚点` 之后的原始目标。 */
  target: string
  /** `#` 之后的锚点（可空）。同文件链接时它就是锚点本身。 */
  anchor: string
  /** `|` 之后的显示别名（可空）。 */
  alias: string
  kind: WikiLinkKind
  /** 显示在页面上的文字：别名优先，其次目标，最后锚点。 */
  label: string
  /** 原始串（不含 `!` 前缀），用于 key 与调试。 */
  raw: string
}

/** `![[x]]` 与 `[[x]]` 的区别只在前缀；解析体共用。 */
const WIKILINK_RE = /\[\[([^\][\n]+)\]\]/g

const IMAGE_EXT = /\.(?:png|jpe?g|gif|webp|svg|bmp|avif)$/i

/** 嵌入（`![[...]]`）与图片扩展名两件事都算 embed。 */
export function isEmbed(target: string, embedded: boolean): boolean {
  return embedded || IMAGE_EXT.test(target.trim())
}

/**
 * 拆一条 wikilink 的内部结构。`raw` 不含 `[[ ]]` 与 `!` 前缀。
 *
 * `[[a#b|c]]` → target `a`、anchor `b`、alias `c`
 * `[[#b]]`   → target 空、anchor `b`（同篇跳转）
 */
function parseInner(raw: string): { target: string; anchor: string; alias: string } {
  const pipeAt = raw.indexOf('|')
  const head = pipeAt >= 0 ? raw.slice(0, pipeAt) : raw
  const alias = pipeAt >= 0 ? raw.slice(pipeAt + 1).trim() : ''

  const hashAt = head.indexOf('#')
  const target = (hashAt >= 0 ? head.slice(0, hashAt) : head).trim()
  const anchor = hashAt >= 0 ? head.slice(hashAt + 1).trim() : ''

  return { target, anchor, alias }
}

/**
 * 扫出一段 markdown 里的所有 wikilink。
 *
 * 代码围栏里的不算（`![[image.png]]` 这种示例不该变成真链接）。
 */
export function findWikiLinks(markdown: string): WikiLink[] {
  const links: WikiLink[] = []
  let fence: string | null = null

  for (const line of markdown.split(/\r?\n/)) {
    const stripped = line.trim()
    const marker = stripped.slice(0, 3)

    if (fence === null && (marker === '```' || marker === '~~~')) {
      fence = marker
      continue
    }
    if (fence !== null) {
      if (marker === fence) fence = null
      continue
    }

    // 逐行扫，避免跨行误配；`!` 前缀在行内任意位置都可能出现。
    const re = new RegExp(WIKILINK_RE.source, 'g')
    let match: RegExpExecArray | null

    while ((match = re.exec(line)) !== null) {
      const before = line.slice(0, match.index)
      // `![[x]]` 的 `!` 紧贴 `[[`；前面有 `!` 才是嵌入。
      const embedded = before.endsWith('!')
      const raw = match[1].trim()
      if (!raw) continue

      const { target, anchor, alias } = parseInner(raw)
      const kind: WikiLinkKind =
        anchor && !target ? 'anchor' : isEmbed(target, embedded) ? 'embed' : 'note'

      links.push({
        alias,
        anchor,
        kind,
        label: alias || target || anchor,
        raw,
        target
      })
    }
  }

  return links
}

/** 全部匹配的起始下标（用于把原文切成「文本 / 链接」两段）。 */
export interface WikiLinkSpan {
  index: number
  length: number
  link: WikiLink
}

/** 带下标的扫描，供渲染层替换用。 */
export function findWikiLinkSpans(markdown: string): WikiLinkSpan[] {
  const spans: WikiLinkSpan[] = []
  let fence: string | null = null

  for (const line of markdown.split(/\r?\n/)) {
    const stripped = line.trim()
    const marker = stripped.slice(0, 3)

    if (fence === null && (marker === '```' || marker === '~~~')) {
      fence = marker
      continue
    }
    if (fence !== null) {
      if (marker === fence) fence = null
      continue
    }

    const re = new RegExp(WIKILINK_RE.source, 'g')
    let match: RegExpExecArray | null

    while ((match = re.exec(line)) !== null) {
      const before = line.slice(0, match.index)
      const embedded = before.endsWith('!')
      const raw = match[1].trim()
      if (!raw) continue

      const { target, anchor, alias } = parseInner(raw)
      const kind: WikiLinkKind =
        anchor && !target ? 'anchor' : isEmbed(target, embedded) ? 'embed' : 'note'

      spans.push({
        index: match.index,
        length: match[0].length,
        link: {
          alias,
          anchor,
          kind,
          label: alias || target || anchor,
          raw,
          target
        }
      })
    }
  }

  return spans
}

/**
 * 建一张「标题 / 文件名 → 笔记」的索引，用来解析跨文件 wikilink。
 *
 * Obsidian 的匹配规则是「按文件名，不按路径」：`[[CATIA]]` 指的是任意目录里
 * 叫 CATIA.md 的那篇。我们照这个来，但索引里同时收**文件名**与**标题**两种
 * 键——盘上的笔记标题常与文件名不同（《微积分.md》的标题是「微积分」，
 * 但《常微分方程基础.md》的标题是「常微分方程基础学习笔记」）。
 */
export interface NoteLinkTarget {
  path: string
  title: string
}

export type NoteLinkIndex = Map<string, NoteLinkTarget>

export function buildNoteLinkIndex(notes: NoteLinkTarget[]): NoteLinkIndex {
  const index: NoteLinkIndex = new Map()

  const put = (key: string, note: NoteLinkTarget): void => {
    const clean = key.trim()
    if (!clean) return
    const lower = clean.toLowerCase()
    // 先到先得：库里同名时保留先出现的那个，不静默覆盖。
    if (!index.has(lower)) index.set(lower, note)
  }

  for (const note of notes) {
    const file = note.path.split('/').pop() ?? ''
    const stem = file.replace(/\.md$/i, '')
    put(stem, note)
    put(note.title, note)
  }

  return index
}

/** 查一张 wikilink 该跳到哪篇笔记；查不到返回 undefined（→ 未创建样式）。 */
export function resolveWikiNote(link: WikiLink, index: NoteLinkIndex): NoteLinkTarget | undefined {
  if (!link.target) return undefined
  return index.get(link.target.trim().toLowerCase())
}
