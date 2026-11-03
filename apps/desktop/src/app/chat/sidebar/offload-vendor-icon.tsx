/**
 * 外包那一格里的**厂商图标**（用户 11-01：「不要是一个方块加一些文字，而是矢量图标加名词」）。
 *
 * 10-22 那一版给每家画一枚 20px 方块：有品牌字形就画字形，没有就印首字母。用户这次点名
 * 换掉那种形状——左栏其它行（分类、会话、项目）全是「一枚矢量图标 + 一个名词」，方块
 * 徽标单独看着像另一个控件。所以这里只出**一枚 16px 的字形**，容器交给调用方的行网格。
 *
 * 不臆造品牌图形：`@icons-pack/react-simple-icons` 只收了 CodeBuddy，其余（Qoder / Marvis /
 * agy / Cursor）没有可靠来源，就用一枚中性的机器人（"外部 AI"），靠旁边的名字区分是谁。
 */

import { SiCodebuddy } from '@icons-pack/react-simple-icons'
import type { ComponentType, SVGProps } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

/** 键 = 后端 `EXTERNAL_AGENTS` 里的那一家（台账 `agent` 那一列）。 */
const VENDOR_GLYPHS: Record<string, ComponentType<SVGProps<SVGSVGElement>>> = {
  codebuddy: SiCodebuddy
}

/** 没有字形的那一家：中性「外部 AI」图标。名词在它右边，不靠图标认人。 */
const FALLBACK_GLYPH = 'robot'

export function OffloadVendorIcon({ className, vendor }: { className?: string, vendor: string }) {
  const Glyph = VENDOR_GLYPHS[(vendor || '').trim()]

  return (
    <span
      aria-hidden="true"
      className={cn('grid size-4 shrink-0 place-items-center text-(--ui-text-tertiary)', className)}
      data-testid={Glyph ? `offload-vendor-glyph-${vendor}` : `offload-vendor-fallback-${vendor}`}
    >
      {Glyph ? <Glyph className="size-3.5" /> : <Codicon name={FALLBACK_GLYPH} size="0.875rem" />}
    </span>
  )
}
