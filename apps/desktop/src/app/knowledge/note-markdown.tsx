import { type ComponentProps, type ElementType, type FC, useEffect, useMemo, useRef } from 'react'
import { Streamdown } from 'streamdown'

import { ExternalLink, ExternalLinkIcon } from '@/lib/external-link'
import { cn } from '@/lib/utils'

import { findAnchor } from './anchor'
import { parseOutline, stripFrontmatter } from './outline'
import { remarkWikilink } from './remark-wikilink'
import { buildNoteLinkIndex, type NoteLinkIndex } from './wikilink'

/**
 * 长笔记专用的 Markdown 排版。
 *
 * 不复用 `CompactMarkdown`：那是聊天气泡里的紧凑渲染，把 h1~h4 全压成
 * 0.74–0.82rem，等于把「第N章 / N.M 节 / 小节」三层书骨架抹平。这里恢复三档，
 * 并在渲染后按 `parseOutline` 的同一份顺序给标题打 id，右栏大纲才能跳准。
 */

const TAG_CLASSES = {
  blockquote: 'my-3 border-l-2 border-(--ui-stroke-secondary) pl-3 italic text-(--ui-text-secondary)',
  h1: 'scroll-mt-4 mt-7 mb-3 text-[1.05rem] font-semibold tracking-tight first:mt-0',
  h2: 'scroll-mt-4 mt-7 mb-3 border-b border-(--ui-stroke-tertiary) pb-1.5 text-[1rem] font-semibold tracking-tight first:mt-0',
  h3: 'scroll-mt-4 mt-5 mb-2 text-[0.9rem] font-semibold',
  h4: 'scroll-mt-4 mt-4 mb-1.5 text-[0.82rem] font-medium text-(--ui-text-secondary)',
  hr: 'my-5 border-(--ui-stroke-tertiary)',
  li: 'marker:text-muted-foreground/60',
  ol: 'mb-3 list-decimal pl-5 last:mb-0',
  p: 'mb-3 text-[0.86rem] leading-[1.85] last:mb-0',
  pre: 'my-3 overflow-x-auto rounded-md border border-border/60 bg-background/70 p-3 font-mono text-xs leading-[1.6]',
  td: 'px-2 py-1 align-top text-[0.78rem] leading-snug',
  th: 'px-2 py-1 text-left text-[0.72rem] font-semibold text-muted-foreground/80',
  thead: 'bg-muted/40',
  ul: 'mb-3 list-disc pl-5 last:mb-0'
} as const

function tagged<T extends keyof typeof TAG_CLASSES>(Tag: T) {
  const Component = (({ className, ...rest }: ComponentProps<T>) => {
    const Element = Tag as ElementType
    return <Element className={cn(TAG_CLASSES[Tag], className)} {...rest} />
  }) as FC<ComponentProps<T>>
  Component.displayName = `NoteMd.${Tag}`
  return Component
}

function MarkdownCode({ className, ...rest }: ComponentProps<'code'>) {
  return (
    <code className={cn('rounded bg-muted/80 px-1 py-px font-mono text-[0.86em]', className)} {...rest} />
  )
}

function MarkdownTable({ className, ...rest }: ComponentProps<'table'>) {
  return (
    <div className="mb-3 max-w-full overflow-x-auto rounded-md border border-border/60 last:mb-0">
      <table className={cn('w-full border-collapse [&_tr]:border-b [&_tr]:border-border/50 last:[&_tr]:border-0', className)} {...rest} />
    </div>
  )
}

/**
 * remark 插件给链接节点带的元数据。
 *
 * ⚠️ 走的是 mdast 已知的 `link` 类型（自定义类型 remark-rehype 不认）。
 * ⚠️⚠️ **元数据只能靠 `href` 带**——实测 Streamdown 重建 `properties` 时只保留
 * `href` / `target` / `rel`，`data-*` 属性会被整个丢掉。所以 href 的 scheme
 * 就是唯一的分流通道：`#wiki-note` / `#wiki-anchor` / `#wiki-embed`，
 * 目标与锚点跟在后面（URL 编码）。
 */
const WIKI_SCHEME = '#wiki-'

interface WikiLinkPayload {
  anchor: string
  kind: 'note' | 'anchor' | 'embed'
  target: string
}

/** 从 href 里解出 wikilink 的元数据；不是 wikilink 就返回 null。 */
function parseWikiHref(href: string | undefined): WikiLinkPayload | null {
  if (!href || !href.startsWith(WIKI_SCHEME)) return null

  const body = href.slice(WIKI_SCHEME.length)
  const separator = body.indexOf(':')
  if (separator < 0) return null

  const kind = body.slice(0, separator)
  if (kind !== 'note' && kind !== 'anchor' && kind !== 'embed') return null

  const rest = body.slice(separator + 1)
  const anchorAt = rest.indexOf('#')
  const target = anchorAt >= 0 ? rest.slice(0, anchorAt) : rest
  const anchor = anchorAt >= 0 ? rest.slice(anchorAt + 1) : ''

  return { anchor: decodeURIComponent(anchor), kind, target: decodeURIComponent(target) }
}

/** 组装 wikilink 的 href（与插件侧同一个函数，别写两份）。 */
export { wikiHref } from './remark-wikilink'

const LINK_BASE =
  'mx-0.5 cursor-pointer rounded px-0.5 align-baseline underline decoration-dotted underline-offset-4 transition-colors'
const LINK_LIVE = 'text-(--ui-text-secondary) decoration-(--ui-text-secondary)/40 hover:text-foreground hover:decoration-foreground/60'
const LINK_BROKEN = 'cursor-default text-(--ui-text-tertiary) decoration-(--ui-text-tertiary)/35'

export interface NoteMarkdownProps {
  className?: string
  /** 库里有哪些笔记——用来判断 `[[CATIA]]` 能不能跳。 */
  linkIndex?: NoteLinkIndex
  /** 同文件锚点跳转：传当前渲染容器的 ref 就行，插件在内部找元素。 */
  onOpenAnchor?: (anchor: string) => void
  /** 跨文件跳转：目标是那篇笔记的逻辑路径。 */
  onOpenNote?: (path: string) => void
  text: string
}

export function NoteMarkdown({ className, linkIndex, onOpenAnchor, onOpenNote, text }: NoteMarkdownProps) {
  const ref = useRef<HTMLDivElement>(null)
  const clean = useMemo(() => stripFrontmatter(text), [text])
  const outline = useMemo(() => parseOutline(text), [text])

  useEffect(() => {
    const root = ref.current
    if (!root) return
    // Streamdown 内部按块渲染，靠组件调用顺序猜 id 不稳；渲染完按 DOM 顺序对齐，
    // 与 parseOutline 用的同一份解析结果逐一对上。
    root.querySelectorAll<HTMLElement>('h1,h2,h3,h4,h5,h6').forEach((element, i) => {
      if (outline[i]) element.id = outline[i].id
    })
  }, [clean, outline])

  const components = useMemo(() => {
    const WikiAnchor = ({ children, className, href, node, ...rest }: ComponentProps<'a'> & { node?: unknown }) => {
      const payload = parseWikiHref(href)

      // 不是 wikilink：普通 markdown 链接按原样处理。
      if (!payload) {
        if (!href || !/^https?:\/\//i.test(href)) {
          return (
            <a className={cn('font-medium underline underline-offset-4 decoration-current/20', className)} href={href} {...rest}>
              {children}
            </a>
          )
        }
        return (
          <ExternalLink className={cn('decoration-current/20', className)} href={href} showExternalIcon={false}>
            {children}
            <ExternalLinkIcon />
          </ExternalLink>
        )
      }

      // 嵌入：盘上只有示例图，没有真的图片文件，所以不假装能显示图——
      // 渲染成一枚安静的占位徽标，写明是嵌入。
      if (payload.kind === 'embed') {
        return (
          <span
            className="mx-0.5 inline-flex items-center gap-1 rounded border border-(--ui-stroke-tertiary) px-1.5 py-px align-baseline text-[0.7rem] text-(--ui-text-tertiary)"
            title={payload.target}
          >
            {payload.target}
          </span>
        )
      }

      const label = String(children ?? payload.target ?? payload.anchor)

      if (payload.kind === 'anchor') {
        return (
          <button className={cn(LINK_BASE, LINK_LIVE)} onClick={() => onOpenAnchor?.(payload.anchor)} type="button">
            {label}
          </button>
        )
      }

      // 跨文件：库里没有这篇笔记时标成「未创建」——静默当普通文字的话，
      // 用户永远发现不了自己写错了链接名。
      const note = payload.target ? linkIndex?.get(payload.target.trim().toLowerCase()) : undefined

      return (
        <button
          className={cn(LINK_BASE, note ? LINK_LIVE : LINK_BROKEN)}
          disabled={!note}
          onClick={() => note && onOpenNote?.(note.path)}
          title={note ? undefined : label}
          type="button"
        >
          {label}
        </button>
      )
    }

    return {
      a: WikiAnchor,
      blockquote: tagged('blockquote'),
      code: MarkdownCode,
      h1: tagged('h1'),
      h2: tagged('h2'),
      h3: tagged('h3'),
      h4: tagged('h4'),
      hr: tagged('hr'),
      li: tagged('li'),
      ol: tagged('ol'),
      p: tagged('p'),
      pre: tagged('pre'),
      table: MarkdownTable,
      td: tagged('td'),
      th: tagged('th'),
      thead: tagged('thead'),
      ul: tagged('ul')
    }
  }, [linkIndex, onOpenAnchor, onOpenNote])

  return (
    <div ref={ref} className={cn('max-w-full text-foreground wrap-anywhere', className)}>
      <Streamdown
        components={components}
        controls={false}
        mode="static"
        parseIncompleteMarkdown={false}
        remarkPlugins={[remarkWikilink]}
      >
        {clean}
      </Streamdown>
    </div>
  )
}

/** 从渲染好的容器里找锚点并滚过去（同文件 wikilink 的落点）。 */
export function jumpToAnchor(container: HTMLElement | null, anchor: string): boolean {
  if (!container) return false
  const found = findAnchor(container, anchor)
  if (!found) return false
  const element = document.getElementById(found.id)
  if (!element) return false
  element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  return true
}

/** 建一张「标题 / 文件名 → 笔记」的索引，给 wikilink 解析用。 */
export function makeLinkIndex(notes: { path: string; title: string }[]): NoteLinkIndex {
  return buildNoteLinkIndex(notes)
}
