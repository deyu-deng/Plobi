import { type ComponentProps, createContext, type ElementType, type FC, useContext, useEffect, useMemo, useRef, useState } from 'react'
import remarkGfm from 'remark-gfm'
import { Streamdown } from 'streamdown'

import { ExternalLink, ExternalLinkIcon } from '@/lib/external-link'
import { createMemoizedMathPlugin } from '@/lib/katex-memo'
import { cn } from '@/lib/utils'

import { findAnchor } from './anchor'
import { decodeAssetSrc, isImageTarget, isRemoteAsset, loadNoteAssetDataUrl } from './note-media'
import { NoteMermaid } from './note-mermaid'
import { parseOutline, stripFrontmatter } from './outline'
import { remarkNoteImages } from './remark-note-images'
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

/**
 * 公式插件（KaTeX）。
 *
 * 与聊天、文件预览**同一份实现**（`@/lib/katex-memo`）——那三处必须口径一致，
 * 不许笔记这边另起一套（纪律 3.3「不新 REST／不另起一套」）。
 *
 * 模块级建一次：插件除了自己的 LRU 之外是无状态的，每次渲染重建只是白建。
 * `singleDollarTextMath: true` ⇒ `$x$` 也当行内公式（与聊天、预览同口径）。
 */
const MATH_PLUGIN = createMemoizedMathPlugin({ singleDollarTextMath: true })

/**
 * Streamdown 的插件与 remark 插件链（模块级建一次，别每次渲染重建）。
 *
 * ⚠️ **`remarkPlugins` 一旦传就是**整个替换**默认那一份**（不是合并）——
 * Streamdown 默认是 `[remarkGfm, codeMeta]`，我们这一侧原来只传了
 * `[remarkWikilink]`，于是 **GFM 被顺带关掉了**：表格、任务列表、删除线全成
 * 原样文字，`MarkdownTable` 那个组件成了死代码。⇒ 这里**必须把 gfm 带上**。
 *
 * `renderers` 按语言名匹配代码块 ⇒ ` ```mermaid ` 走我们那份渲染器出图，
 * 而不用去覆盖 `code`／`pre` 组件（覆盖了就把普通代码块的既有形状也搭进去）。
 */
const NOTE_PLUGINS = {
  math: MATH_PLUGIN,
  renderers: [{ component: NoteMermaid, language: 'mermaid' }]
}

const NOTE_REMARK_PLUGINS = [remarkGfm, remarkNoteImages, remarkWikilink]


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
 * 列表项：任务列表（`- [ ]`）不要那个圆点标记。
 *
 * GFM 的任务列表会带 `task-list-item` 类；不摘掉标记的话，勾选框左边还杵着
 * 一个圆点，看着像两个东西。
 */
function MarkdownListItem({ className, ...rest }: ComponentProps<'li'>) {
  const task = /task-list-item/.test(className ?? '')

  return <li className={cn(task ? 'list-none' : 'marker:text-muted-foreground/60', className)} {...rest} />
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
  if (!href || !href.startsWith(WIKI_SCHEME)) {return null}

  const body = href.slice(WIKI_SCHEME.length)
  const separator = body.indexOf(':')

  if (separator < 0) {return null}

  const kind = body.slice(0, separator)

  if (kind !== 'note' && kind !== 'anchor' && kind !== 'embed') {return null}

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

/**
 * 这一篇的「图片落点上下文」：库根与笔记目录。
 *
 * 走 context 而不是闭包：渲染组件是 `useMemo` 里造的，闭包会把外面那两格
 * 变成「外层作用域的值」，eslint 的 react-hooks 规则不认它们当依赖
 * （而且真出问题时也难查）。context 一给，两边都干净。
 */
const NoteAssetContext = createContext<{ assetRoot?: string; baseDir?: string }>({})

export interface NoteMarkdownProps {
  className?: string
  /** 笔记库根（盘上绝对路径）：相对图片路径不许越出它。 */
  assetRoot?: string
  /** 这篇笔记自己的目录：相对路径先从这里找。 */
  baseDir?: string
  /** 库里有哪些笔记——用来判断 `[[CATIA]]` 能不能跳。 */
  linkIndex?: NoteLinkIndex
  /** 同文件锚点跳转：传当前渲染容器的 ref 就行，插件在内部找元素。 */
  onOpenAnchor?: (anchor: string) => void
  /** 跨文件跳转：目标是那篇笔记的逻辑路径。 */
  onOpenNote?: (path: string) => void
  text: string
}

export function NoteMarkdown({
  assetRoot,
  baseDir,
  className,
  linkIndex,
  onOpenAnchor,
  onOpenNote,
  text
}: NoteMarkdownProps) {
  const ref = useRef<HTMLDivElement>(null)
  const clean = useMemo(() => stripFrontmatter(text), [text])
  const outline = useMemo(() => parseOutline(text), [text])

  useEffect(() => {
    const root = ref.current

    if (!root) {return}
    // Streamdown 内部按块渲染，靠组件调用顺序猜 id 不稳；渲染完按 DOM 顺序对齐，
    // 与 parseOutline 用的同一份解析结果逐一对上。
    root.querySelectorAll<HTMLElement>('h1,h2,h3,h4,h5,h6').forEach((element, i) => {
      if (outline[i]) {element.id = outline[i].id}
    })
  }, [clean, outline])

  const components = useMemo(() => {
    /**
     * 笔记里的一张本地图片。
     *
     * `target` 是用户写的那串引用（`图.png`／`attachments/图.png`）。按
     * 「笔记同目录 → 同目录的 attachments/ → 库根」去盘上找，读成 data URL ——
     * 渲染进程的基址不是笔记库，相对路径交给浏览器一定是碎图。
     *
     * 读不到就**照实说**：落回一枚带文件名的占位，不拿碎图标糊过去。
     */
    const NoteImage = ({ alt, target, width }: { alt?: string; target: string; width?: string }) => {
      const { assetRoot, baseDir } = useContext(NoteAssetContext)
      const [dataUrl, setDataUrl] = useState<string | null>(null)
      const [missing, setMissing] = useState(false)
      const remote = isRemoteAsset(target)

      useEffect(() => {
        if (remote) {
          setDataUrl(null)
          setMissing(false)

          return
        }

        let alive = true

        setDataUrl(null)
        setMissing(false)

        void loadNoteAssetDataUrl(assetRoot, baseDir, target).then(url => {
          if (!alive) {return}

          if (url) {
            setDataUrl(url)
          } else {
            setMissing(true)
          }
        })

        return () => {
          alive = false
        }
      }, [target, assetRoot, baseDir, remote])

      const label = alt || target || '图片'

      if (remote) {
        return <img alt={label} className="my-2 block max-w-full rounded-lg" src={target} style={width ? { width } : undefined} />
      }

      if (missing) {
        return (
          <span
            className="my-2 inline-flex items-center gap-1 rounded border border-(--ui-stroke-tertiary) px-1.5 py-px align-baseline text-[0.7rem] text-(--ui-text-tertiary)"
            title={target}
          >
            {label}
          </span>
        )
      }

      if (!dataUrl) {
        return <span className="my-2 block h-24 w-48 animate-pulse rounded-md bg-muted" />
      }

      return <img alt={label} className="my-2 block max-w-full rounded-lg" src={dataUrl} style={width ? { width } : undefined} />
    }

    /**
     * Markdown 的图片标签。
     *
     * 本地引用在 remark 那层已经被编成 `streamdown:`（见 `remark-note-images.ts`），
     * 这里解回来交给 `NoteImage` 去盘上找；不是本地引用（外链）就原样给浏览器。
     */
    const MarkdownImage = ({ alt, src, className, ...rest }: ComponentProps<'img'>) => {
      const target = decodeAssetSrc(src)

      if (target === null) {
        return <img alt={alt ?? ''} className={cn('my-2 block max-w-full rounded-lg', className)} src={src} {...rest} />
      }

      return <NoteImage alt={alt} target={target} />
    }

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

      // 嵌入：图片画出来（Obsidian 的 `![[图.png]]` 就是插图），别的落成一枚
      // 安静的占位徽标——盘上确实没有那一份时**不假装能显示**。
      if (payload.kind === 'embed') {
        if (isImageTarget(payload.target)) {
          // `![[图.png|300]]` 里的竖线后面是宽度（Obsidian 的写法），不是别名。
          const width = /^\d+$/.test(String(children ?? '').trim()) ? `${String(children).trim()}px` : undefined

          return <NoteImage alt={payload.target} target={payload.target} width={width} />
        }

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
      img: MarkdownImage,
      li: MarkdownListItem,
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

  const assets = useMemo(() => ({ assetRoot, baseDir }), [assetRoot, baseDir])

  return (
    <div className={cn('max-w-full text-foreground wrap-anywhere', className)} ref={ref}>
      <NoteAssetContext.Provider value={assets}>
        <Streamdown
          components={components}
          controls={false}
          mode="static"
          parseIncompleteMarkdown={false}
          plugins={NOTE_PLUGINS}
          remarkPlugins={NOTE_REMARK_PLUGINS}
        >
          {clean}
        </Streamdown>
      </NoteAssetContext.Provider>
    </div>
  )
}

/** 从渲染好的容器里找锚点并滚过去（同文件 wikilink 的落点）。 */
export function jumpToAnchor(container: HTMLElement | null, anchor: string): boolean {
  if (!container) {return false}
  const found = findAnchor(container, anchor)

  if (!found) {return false}
  const element = document.getElementById(found.id)

  if (!element) {return false}
  element.scrollIntoView({ behavior: 'smooth', block: 'start' })

  return true
}

/** 建一张「标题 / 文件名 → 笔记」的索引，给 wikilink 解析用。 */
export function makeLinkIndex(notes: { path: string; title: string }[]): NoteLinkIndex {
  return buildNoteLinkIndex(notes)
}
