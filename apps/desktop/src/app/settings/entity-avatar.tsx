import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * 配置卡第一格那颗图标（`WP-SETTINGS-CARD-UNIFY`，用户 11-07 拍「统一共用」）。
 *
 * 从前这件事写了两遍：信源那族 `SourceAvatar`（24px）与订阅那族 `QuotaAppIcon`（32px），
 * 形状同一件事——品牌字形或首字母＋品牌色底染、抓来的 favicon 不再叠底染。这里只留一份壳，
 * 两族各自的品牌表留在自己档里、把字形递进来。**尺寸归语法不归调用方**：默认 32px（卡片语法 §1），
 * 密列表（会话名单那种）才显式要 24px。
 */
export function EntityAvatar({
  className,
  color,
  glyph,
  iconUrl,
  monogram,
  size = 'md',
  testId
}: {
  className?: string
  /** 品牌色。给了就 16% 底染；不给就是中性底（第三方/没认领的那一支）。 */
  color?: string
  /** 品牌字形或通用形状（地球/信封那种）。没有就落 monogram。 */
  glyph?: ReactNode
  /** 抓来的 favicon / 官方标志 URL。给了就只画它，不叠底染。 */
  iconUrl?: string
  monogram: string
  size?: 'md' | 'sm'
  testId?: string
}) {
  const base = cn(
    'inline-grid shrink-0 place-items-center overflow-hidden',
    size === 'md'
      ? 'size-8 rounded-lg border border-[color-mix(in_srgb,var(--dt-foreground)_10%,transparent)]'
      : 'size-6 rounded-md',
    className
  )

  if (iconUrl) {
    return (
      <span aria-hidden="true" className={base} data-testid={testId}>
        <img alt="" className={size === 'md' ? 'size-5 object-contain' : 'size-4 object-contain'} src={iconUrl} />
      </span>
    )
  }

  return (
    <span
      aria-hidden="true"
      className={cn(base, !color && 'bg-(--ui-bg-tertiary) text-(--ui-text-tertiary)')}
      data-testid={testId}
      style={color ? { backgroundColor: `color-mix(in srgb, ${color} 16%, transparent)`, color } : undefined}
    >
      {glyph ?? (
        <span className={size === 'md' ? 'text-[0.8rem] font-semibold' : 'text-[length:var(--conversation-caption-font-size)] font-medium'}>
          {monogram}
        </span>
      )}
    </span>
  )
}
