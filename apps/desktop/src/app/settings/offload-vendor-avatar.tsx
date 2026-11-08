/**
 * 外包那一屏第一格的品牌方块（卡片语法 §1 ①：`Docs/specs/settings-card-system.md`）。
 *
 * 品牌表只有一份：`quota-app-icon.tsx` 那张（hub 的腿 id → 字形／官方标志／品牌色）。
 * 这一屏手上有的是**厂商 id**（agy／claude／qwen…），那张表认的是**腿 id**
 * （antigravity／claude_code_cli／qwen-cli…）——两套 id 的对照表在这里，
 * 屏上两边都不出现（屏上只写品牌名，见 `vendorLabelFor`）。
 *
 * 没有对应腿 id 的那几家（Copilot CLI、Kilo Code CLI）没有可发布的品牌字形：
 * 按卡片语法 §3「退首字母不是将就，是裁定」落中性底 ＋ 名字首字母，
 * **不搜第三方画的 logo** 替它认领身份。
 */

import { vendorLabelFor } from '@/app/chat/sidebar/offload-vendor-icon'

import { QuotaAppIcon } from './quota-app-icon'

/** 厂商 id → hub 的腿 id（品牌表那一把键）。查不到 ⇒ 中性底 ＋ 首字母。 */
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

/**
 * 一家外包软件的那颗方块。形状归 `EntityAvatar`（`QuotaAppIcon` 的薄壳）：
 * 32px／`rounded-lg`／发丝边／品牌色 16% 底染；品牌字形优先，没有字形退首字母。
 */
export function OffloadVendorAvatar({ name }: { name: string }) {
  const vendor = (name || '').trim()

  return (
    <span data-testid={`offload-vendor-avatar-${vendor}`}>
      <QuotaAppIcon id={VENDOR_LEG_ID[vendor] ?? ''} name={vendorLabelFor(vendor)} />
    </span>
  )
}
