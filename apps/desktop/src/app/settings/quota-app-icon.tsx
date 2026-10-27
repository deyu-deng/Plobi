/**
 * 订阅软件卡片上的那颗图标（用户 10-27：「一个卡片显示对应的图标和简介」）。
 *
 * 形状与 `settings/sources/source-icon.tsx` 同一族：品牌字形优先，**没有字形就退回
 * 品牌色底染 + 首字母**（那不是将就——钉钉、Kiro 这类品牌方没有可发布的字形，画一个
 * 假 logo 等于替它认领身份）。表在这里，不在 hub：图标是装饰，缺了不影响那条腿能不能用。
 */

// 逐个字形深引，不走包的 barrel：`@icons-pack/react-simple-icons` 的 index 重导出约
// 三千个模块，冷启动时整片进转换图。2026-10-27 在这台实测过一次——`providers-settings`
// 的测试档一旦把 barrel 拉进图里，第一个用例光等模块解析就超过 5 s 超时线。
import SiCline from '@icons-pack/react-simple-icons/icons/SiCline'
import SiCodebuddy from '@icons-pack/react-simple-icons/icons/SiCodebuddy'
import SiCursor from '@icons-pack/react-simple-icons/icons/SiCursor'
import SiGooglegemini from '@icons-pack/react-simple-icons/icons/SiGooglegemini'
import SiOpencode from '@icons-pack/react-simple-icons/icons/SiOpencode'
import SiQwen from '@icons-pack/react-simple-icons/icons/SiQwen'
import type { ComponentType, SVGProps } from 'react'

import { cn } from '@/lib/utils'

interface QuotaIconSpec {
  // `title` 是 simple-icons 那一族自己加的无障碍属性（SVG 规范里没有 `<svg title>`），
  // 所以类型要写全，不能只当 `SVGProps`。
  Icon?: ComponentType<SVGProps<SVGSVGElement> & { title?: string }>
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
        // `title=""`：字形是装饰（整颗 `aria-hidden`），而 simple-icons 无条件渲染一个
        // `<title>`，默认值是品牌名——那会在屏上多出第二个"CodeBuddy"，读屏也念两遍。
        <spec.Icon aria-hidden className="size-4" color={spec.color} title="" />
      ) : (
        <span className="text-[0.8rem] font-semibold" style={{ color: spec?.color }}>
          {monogram}
        </span>
      )}
    </span>
  )
}
