import { lazy, Suspense } from 'react'

/**
 * 笔记里的 mermaid 图。
 *
 * 复用聊天那份实现（`components/assistant-ui/embeds/mermaid-embed.tsx`）：
 * 那是全仓唯一一份 mermaid 渲染器，`mermaid` 本体也是既有依赖 ⇒ 不引新依赖、
 * 不另起一套（纪律 3.2「不降级」／3.3「不另起第二套」）。挂法走 Streamdown 的
 * `plugins.renderers` 口子（按语言名匹配），所以不必去覆盖 `code`／`pre` 组件。
 *
 * 懒加载：mermaid 库不小，笔记栏不该为一份可能永远不出现的图付首屏。
 */
const MermaidRenderer = lazy(() => import('@/components/assistant-ui/embeds/mermaid-embed'))

export interface NoteMermaidProps {
  code: string
}

export function NoteMermaid({ code }: NoteMermaidProps) {
  return (
    <Suspense
      fallback={
        <pre className="my-3 overflow-x-auto rounded-md border border-border/60 bg-background/70 p-3 font-mono text-xs leading-[1.6]">
          {code}
        </pre>
      }
    >
      <MermaidRenderer code={code} streaming={false} />
    </Suspense>
  )
}
