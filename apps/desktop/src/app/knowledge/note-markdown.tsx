import { type ComponentProps, type ElementType, type FC, useEffect, useMemo, useRef } from 'react'
import { Streamdown } from 'streamdown'

import { ExternalLink, ExternalLinkIcon } from '@/lib/external-link'
import { cn } from '@/lib/utils'

import { parseOutline, stripFrontmatter } from './outline'

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

function MarkdownAnchor({ children, className, href, ...rest }: ComponentProps<'a'>) {
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

function MarkdownCode({ className, ...rest }: ComponentProps<'code'>) {
  return <code className={cn('rounded bg-muted/80 px-1 py-px font-mono text-[0.86em]', className)} {...rest} />
}

function MarkdownTable({ className, ...rest }: ComponentProps<'table'>) {
  return (
    <div className="mb-3 max-w-full overflow-x-auto rounded-md border border-border/60 last:mb-0">
      <table className={cn('w-full border-collapse [&_tr]:border-b [&_tr]:border-border/50 last:[&_tr]:border-0', className)} {...rest} />
    </div>
  )
}

const COMPONENTS = {
  a: MarkdownAnchor,
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

export function NoteMarkdown({ className, text }: { className?: string; text: string }) {
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

  return (
    <div ref={ref} className={cn('max-w-full text-foreground wrap-anywhere', className)}>
      <Streamdown components={COMPONENTS} controls={false} mode="static" parseIncompleteMarkdown={false}>
        {clean}
      </Streamdown>
    </div>
  )
}
