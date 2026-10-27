/**
 * 订阅软件卡片上的那颗图标（用户 10-27：「一个卡片显示对应的图标和简介」）。
 *
 * 形状与 `settings/sources/source-icon.tsx` 同一族：品牌字形优先，**没有字形就退回
 * 品牌色底染 + 首字母**（那不是将就——钉钉、Kiro 这类品牌方没有可发布的字形，画一个
 * 假 logo 等于替它认领身份）。表在这里，不在 hub：图标是装饰，缺了不影响那条腿能不能用。
 */

import { SiCline, SiCodebuddy, SiCursor, SiGooglegemini, SiOpencode, SiQwen } from '@icons-pack/react-simple-icons'
import type { ComponentType, SVGProps } from 'react'

import { cn } from '@/lib/utils'

interface QuotaIconSpec {
  Icon?: ComponentType<SVGProps<SVGSVGElement>>
  color: string
}

/** 键 = hub 的腿 id（`/plobi/auth` 的 id）。查不到 ⇒ 中性底 + 名字首字母。 */
const QUOTA_ICONS: Record<string, QuotaIconSpec> = {
  antigravity: { color: '#4285F4' },
  claude_code_cli: { color: '#D97757' },
  cline_cli: { Icon: SiCline, color: '#171412' },
  codex_cli: { color: '#10A37F' },
  cursor: { Icon: SiCursor, color: '#7B61FF' },
  gemini_cli: { Icon: SiGooglegemini, color: '#4285F4' },
  'gemini-cli': { Icon: SiGooglegemini, color: '#4285F4' },
  kiro_cli: { color: '#FF9900' },
  opencode_cli: { Icon: SiOpencode, color: '#1B1D21' },
  qwen_cli: { Icon: SiQwen, color: '#615CED' },
  'qwen-cli': { Icon: SiQwen, color: '#615CED' },
  workbuddy: { Icon: SiCodebuddy, color: '#0052D9' },
  workbuddy_api: { Icon: SiCodebuddy, color: '#0052D9' }
}

export function QuotaAppIcon({ className, id, name }: { className?: string; id: string; name: string }) {
  const spec = QUOTA_ICONS[id]
  const monogram = (name || id || '?').trim().charAt(0).toUpperCase()

  return (
    <span
      aria-hidden
      className={cn(
        'flex size-8 shrink-0 items-center justify-center rounded-lg border border-[color-mix(in_srgb,var(--dt-foreground)_10%,transparent)]',
        className
      )}
      // 品牌色底染 16%：字形在任何底色上都读得出来，又不压过那一行本身。
      style={{ backgroundColor: `color-mix(in srgb, ${spec?.color ?? 'var(--dt-foreground)'} 16%, transparent)` }}
    >
      {spec?.Icon ? (
        <spec.Icon aria-hidden className="size-4" color={spec.color} />
      ) : (
        <span className="text-[0.8rem] font-semibold" style={{ color: spec?.color }}>
          {monogram}
        </span>
      )}
    </span>
  )
}
