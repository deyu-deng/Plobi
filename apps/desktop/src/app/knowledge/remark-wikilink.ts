/**
 * remark 插件：把 Obsidian 风格的 `[[wikilink]]` 变成真正的 AST 节点。
 *
 * 为什么走 AST 而不是字符串替换：`[[a|b]]` 不是标准 markdown，字符串层面
 * 处理要自己躲代码围栏、行内代码、表格单元格里各种坑；而 remark 已经把
 * 文档解析成了 mdast——围栏里的内容在 AST 里本来就是 `code` 节点，**天然
 * 不会被走到**。
 *
 * 产出的节点用 `data.hName` / `data.hProperties` 定制，于是渲染层只需要在
 * `components` 里认这三个 tag：
 * - `wikilink-note` —— 跨文件链接
 * - `wikilink-anchor` —— 同文件锚点链接
 * - `wikilink-embed` —— 嵌入（`![[…]]`）
 *
 * 目标解析**不在插件里做**：插件不知道笔记库里有哪些文件。渲染层拿
 * `data.hProperties.target` 自己去查，查不到就标「未创建」。
 */

export interface WikiLinkData {
  target: string
  anchor: string
  alias: string
  kind: 'note' | 'anchor' | 'embed'
}

const WIKILINK_RE = /\[\[([^\][\n]+)\]\]/g

const IMAGE_EXT = /\.(?:png|jpe?g|gif|webp|svg|bmp|avif)$/i

type TextNode = {
  type: string
  value?: string
  children?: unknown[]
  data?: Record<string, unknown>
}

type ParentNode = {
  type: string
  children?: unknown[]
}

interface ParsedPart {
  kind: WikiLinkData['kind']
  text: string
  /** 这是**普通文字**片段（不是链接）。链接片段 `isLink` 为 true。 */
  isLink: boolean
  /** 嵌入标记 `!` 已经从文本里摘掉了。 */
  bang: boolean
}

/**
 * 把一段纯文本按 wikilink 切开。
 *
 * `![[x]]` 的 `!` 紧贴 `[[`——嵌入。图片扩展名也算嵌入（不管有没有 `!`）。
 *
 * ⚠️ 普通文字片段与 `note` 类型的链接**必须分开标记**（`isLink`）：两者都是
 * `kind: 'note'`，靠 kind 分不开——第一版就是栽在这，`[[Creo]]` 被当成普通
 * 文字推了出去，链接根本没生成。
 */
export function splitWikilinks(value: string): ParsedPart[] {
  const out: ParsedPart[] = []
  let cursor = 0
  let match: RegExpExecArray | null

  WIKILINK_RE.lastIndex = 0
  while ((match = WIKILINK_RE.exec(value)) !== null) {
    const raw = match[1].trim()
    if (!raw) continue

    const before = value.slice(cursor, match.index)
    const bang = before.endsWith('!')
    const head = before.slice(0, bang ? -1 : undefined)

    if (head) out.push({ bang: false, isLink: false, kind: 'note', text: head })

    const pipeAt = raw.indexOf('|')
    const linkHead = pipeAt >= 0 ? raw.slice(0, pipeAt) : raw
    const hashAt = linkHead.indexOf('#')
    const target = (hashAt >= 0 ? linkHead.slice(0, hashAt) : linkHead).trim()

    const kind: WikiLinkData['kind'] =
      hashAt >= 0 && !target ? 'anchor' : bang || IMAGE_EXT.test(target) ? 'embed' : 'note'

    out.push({ bang, isLink: true, kind, text: raw })
    cursor = match.index + match[0].length
  }

  if (cursor < value.length) out.push({ bang: false, isLink: false, kind: 'note', text: value.slice(cursor) })
  return out
}

function parseLinkText(raw: string): { alias: string; anchor: string; target: string } {
  const pipeAt = raw.indexOf('|')
  const head = pipeAt >= 0 ? raw.slice(0, pipeAt) : raw
  const alias = pipeAt >= 0 ? raw.slice(pipeAt + 1).trim() : ''
  const hashAt = head.indexOf('#')

  return {
    alias,
    anchor: hashAt >= 0 ? head.slice(hashAt + 1).trim() : '',
    target: (hashAt >= 0 ? head.slice(0, hashAt) : head).trim()
  }
}

/**
 * remark 插件本体。
 *
 * 只处理 `text` 节点——标题、段落、列表项、表格单元格里的行内文字都会走到；
 * 代码块（`code` 节点）与行内代码（`inlineCode` 节点）不在其中，
 * 所以示例代码里的 `[[…]]` 不会被误当成链接。
 *
 * 替换的做法是**改父节点的 children**：把原来那个 text 节点换成
 * 「文本片段 + 链接节点」的序列。直接往 text 节点里塞 children 是错的——
 * mdast 的 text 节点没有 children 字段。
 *
 * ⚠️ 节点类型必须是 remark-rehype **认识的已知类型**（这里用 `link`）。
 * 自造一个 `wikilink` 类型行不通：`data.hName` 只在已知类型的 handler 里
 * 被消费，未知类型会走 `unknown` handler，把 data 当普通属性吐到 DOM 上
 * （渲染出来是 `<p node="[object Object]">`）。所以这里用 `link` + 假的
 * `url`，真正的元数据走 `data.hProperties`。
 */
export function remarkWikilink() {
  return (tree: unknown): void => {
    walk(tree as unknown as ParentNode)
  }
}

/**
 * 自己递归，不用 `visit`。
 *
 * `visit` 的回调返回值有特殊语义（返回数字 = 下一个要访问的下标，且会打断
 * 当前父节点的兄弟遍历）。而这里要做的恰恰是**就地替换父节点的 children**——
 * 替换完游标就错位了，跟 visit 的语义天生别扭。上一版就是栽在这：插件提前
 * 返回 undefined，后续段落的 wikilink 全没被处理。
 *
 * 自己递归没有这个坑：替换完直接处理新的 children 序列。
 */
function walk(node: ParentNode): void {
  const children = node.children
  if (!Array.isArray(children)) return

  const next: unknown[] = []

  for (const child of children) {
    const typed = child as TextNode

    if (typed.type === 'text' && typeof typed.value === 'string' && typed.value.includes('[[')) {
      next.push(...expandText(typed.value))
      continue
    }

    next.push(child)

    // 递归进子节点（段落、列表项、表格单元格里的行内文字都在下面）。
    if (Array.isArray(typed.children)) walk(typed)
  }

  node.children = next
}

/** 把一段文本按 wikilink 切开，产出「文本 / 链接」交替的节点序列。 */
function expandText(value: string): unknown[] {
  const parts = splitWikilinks(value)
  if (parts.length === 0) return []

  const out: unknown[] = []

  for (const part of parts) {
    if (!part.isLink) {
      if (part.text) out.push({ type: 'text', value: part.text })
      continue
    }

    const { alias, anchor, target } = parseLinkText(part.text)
    const label = alias || target || anchor

    out.push({
      children: [{ type: 'text', value: label }],
      // ⚠️ 元数据只能靠 href 带：实测 Streamdown 重建 `properties` 时只保留
      // `href` / `target` / `rel`，`data-*` 会被整个丢掉。所以 href 的 scheme
      // 就是唯一的分流通道——`#wiki-note:` / `#wiki-anchor:` / `#wiki-embed:`，
      // 后面跟 URL 编码的目标与锚点。
      type: 'link',
      url: wikiHref(part.kind, target, anchor)
    })
  }

  return out
}

/** 组装 wikilink 的 href（与 `note-markdown.tsx` 的解析一一对应）。 */
export function wikiHref(kind: 'note' | 'anchor' | 'embed', target: string, anchor: string): string {
  const suffix = anchor ? `#${encodeURIComponent(anchor)}` : ''
  return `#wiki-${kind}:${encodeURIComponent(target)}${suffix}`
}
