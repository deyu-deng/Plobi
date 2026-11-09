/**
 * 外包那一格里的**厂商图标**与**品牌方块**。
 *
 * 两件东西，两个形状，别混：
 *
 * ① `VendorBrandBlock` —— **品牌方块**（用户 11-09：「分组、行、点开读按 Providers 那一屏同形」）：
 *    与设置屏 Providers 那一屏**同一颗**（`QuotaAppIcon` → `EntityAvatar`）：品牌字形优先，
 *    没有字形落中性底 ＋ 首字母，**不拿一枚「机器人」图标替它认领身份**。
 * ② `OffloadVendorIcon` —— 16px 的中性矢量图标。**只作退路**：那家没有品牌方块可画时
 *    （不在那张品牌表里），用一枚中性的「外部 AI」图标，名字在它右边，不靠图标认人。
 *
 * 不臆造品牌图形：`@icons-pack/react-simple-icons` 只收了 CodeBuddy，其余（Qoder / Marvis /
 * Cursor / agy）的字形与官方标志由**订阅那张品牌表**（`quota-app-icon.tsx`）提供。
 */

import { SiCodebuddy } from '@icons-pack/react-simple-icons'
import type { ComponentType, SVGProps } from 'react'

import { QuotaAppIcon } from '@/app/settings/quota-app-icon'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

/** 键 = 后端 `EXTERNAL_AGENTS` 里的那一家（台账 `agent` 那一列）。 */
const VENDOR_GLYPHS: Record<string, ComponentType<SVGProps<SVGSVGElement>>> = {
  codebuddy: SiCodebuddy
}

/**
 * 内部 id → 屏幕上那一个字（用户 10-30：「agy 能不能用别人的官方首字母大写名称」）。
 *
 * **只有屏幕用品牌名**：台账、tooltip 里的二进制名与旗标、内部 id 一律不动
 * （`agy` 那一行内部还是 `agy`，屏上写 Antigravity）。
 * 回落到原样的 id——**不许变成空白**：一个空白行等于让用户以为那一格坏了。
 */
const VENDOR_LABELS: Record<string, string> = {
  agy: 'Antigravity',
  claude: 'Claude Code',
  cline: 'Cline',
  codebuddy: 'CodeBuddy',
  codex: 'Codex',
  copilot: 'GitHub Copilot CLI',
  cursor: 'Cursor',
  gemini: 'Gemini CLI',
  kilo: 'Kilo Code',
  kiro: 'Kiro',
  marvis: 'Marvis',
  opencode: 'OpenCode',
  qoder: 'Qoder',
  qwen: 'Qwen Code'
}

/** 屏幕上那一家的名字。查不到就原样返回内部 id。 */
export function vendorLabelFor(vendor: string): string {
  const key = (vendor || '').trim()

  return VENDOR_LABELS[key] ?? key
}

/**
 * 厂商 id → 订阅那一张品牌表的**腿 id**（`quota-app-icon.tsx` 那把键）。
 *
 * 屏上两边拿的是**厂商 id**（marvis／cursor／agy…），那张品牌表认的是**腿 id**
 * （antigravity／qoder／claude_code_cli…）——两套 id 的对照只留这一份，
 * 设置屏那一屏（`offload-vendor-avatar.tsx`）与左栏这一屏都从这儿取。
 */
const VENDOR_LEG_ID: Record<string, string> = {
  agy: 'antigravity',
  claude: 'claude_code_cli',
  cline: 'cline_cli',
  codebuddy: 'workbuddy',
  codex: 'codex_cli',
  cursor: 'cursor',
  gemini: 'gemini_api',
  kiro: 'kiro_cli',
  opencode: 'opencode_cli',
  qoder: 'qoder',
  qwen: 'qwen-cli'
}

/** 厂商 id → 腿 id。查不到 ⇒ 空串（那一格落中性底 ＋ 首字母，不硬凑一家）。 */
export function vendorLegIdFor(vendor: string): string {
  return VENDOR_LEG_ID[(vendor || '').trim()] ?? ''
}

/**
 * 一家外包软件的那颗**品牌方块**（与 Providers 那一屏同一件：`QuotaAppIcon` → `EntityAvatar`）。
 *
 * 尺寸分两档，**归语法不归调用方随手写**：
 * - `md`（32px，`rounded-lg`，发丝边）＝卡片那一档（设置屏 Providers 那一屏）。
 * - `sm`（24px，`rounded-md`，无边）＝**密列表**那一档（左栏的行只有 28px 高，32px 塞不进去）。
 *
 * 没有品牌字形的那几家（Marvis／Kilo…）按卡片语法 §3 落**中性底 ＋ 首字母**。
 */
export function VendorBrandBlock({
  className,
  size = 'sm',
  vendor
}: {
  className?: string
  size?: 'md' | 'sm'
  vendor: string
}) {
  const key = (vendor || '').trim()

  return (
    <span data-testid={`vendor-brand-block-${key}`}>
      <QuotaAppIcon
        className={cn(size === 'sm' ? 'size-6 rounded-md border-0' : '', className)}
        id={vendorLegIdFor(key)}
        name={vendorLabelFor(key)}
      />
    </span>
  )
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
