/**
 * 外包那一格里的**厂商徽标**（用户 10-22：「我没有看到界面上有显示 Qoder 的图标」）。
 *
 * 原先这一格对每一家都画同一枚通用火箭，等于把"我派给了谁"这件事又压回了一行小字。
 *
 * **为什么是本地一张表**：与 `settings/sources/source-icon.tsx` 同一个理由——图标是装饰，
 * 不是功能。让它进后端声明就等于把"加一家外部软件不用改前端"那条验收为一个颜色破掉。
 *
 * **为什么不臆造品牌色**：`@icons-pack/react-simple-icons` 的 React 包装不带 `hex`
 * （10-22 现读：`SiCodebuddy.hex === undefined`），而 simple-icons 的**单文件数据**在这台的
 * 安装布局里也解析不到。没有可靠来源就**不填颜色**：随便挑一个色等于替那个品牌认领一个
 * 它自己没用的身份（信源那一族对校徽持同一立场）。所以这里只有两种诚实形状——
 * 有字形就用字形（`SiCodebuddy`），没有就用首字母 + 主题中性色（Qoder 还没进 simple-icons）。
 *
 * `iconUrl` 那一格先留着不填：信源那一族已经证明"抓网站自己的 favicon"是可行的，
 * 外部软件哪天要真品牌图形，走同一条口，不必再动这里的布局。
 */

import { SiCodebuddy } from '@icons-pack/react-simple-icons'
import type { ComponentType, SVGProps } from 'react'

import { cn } from '@/lib/utils'

type VendorIconSpec = {
  Icon?: ComponentType<SVGProps<SVGSVGElement>>
  /** 没有字形时的首字母徽标。有 `Icon` 时这一格不参与渲染。 */
  monogram: string
}

/** 键 = 后端 `EXTERNAL_AGENTS` 里的那一家（台账 `agent` 那一列）。查不到走回落。 */
const VENDOR_ICONS: Record<string, VendorIconSpec> = {
  agy: { monogram: 'A' },
  codebuddy: { Icon: SiCodebuddy, monogram: 'C' },
  // 品牌方还没进 simple-icons ⇒ 首字母。**不自己画一个"Qoder 的样子"**。
  qoder: { monogram: 'Q' },
}

export function hasVendorIcon(vendor: string): boolean {
  return Object.prototype.hasOwnProperty.call(VENDOR_ICONS, vendor)
}

/** 首字母回落用的就是名字本身的首字符——表里没登记的一家也不许变成空白方块。 */
export function vendorMonogramFor(vendor: string): string {
  const trimmed = (vendor || '').trim()

  if (!trimmed) {
    return '?'
  }

  return VENDOR_ICONS[trimmed]?.monogram ?? trimmed.slice(0, 1).toUpperCase()
}

export function OffloadVendorAvatar({
  className,
  iconUrl,
  vendor,
}: {
  className?: string
  /** 预留给"抓厂商自己网站的 favicon"（与信源那一族同形）。空/缺＝没有。 */
  iconUrl?: string
  vendor: string
}) {
  const spec = VENDOR_ICONS[vendor]

  const base = cn(
    'inline-grid size-5 shrink-0 place-items-center overflow-hidden rounded-md',
    'bg-(--ui-bg-secondary) text-[0.62rem] font-semibold text-(--ui-text-secondary)',
    className,
  )

  if (iconUrl) {
    return (
      <span aria-hidden="true" className={base} data-testid={`offload-vendor-img-${vendor}`}>
        <img alt="" className="size-3.5 object-contain" src={iconUrl} />
      </span>
    )
  }

  if (spec?.Icon) {
    const Icon = spec.Icon

    return (
      <span aria-hidden="true" className={base} data-testid={`offload-vendor-glyph-${vendor}`}>
        <Icon className="size-3.5" />
      </span>
    )
  }

  return (
    <span
      aria-hidden="true"
      className={base}
      data-testid={`offload-vendor-monogram-${vendor}`}
    >
      {vendorMonogramFor(vendor)}
    </span>
  )
}
