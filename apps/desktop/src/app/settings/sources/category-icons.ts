/**
 * Sources 每一大类的那颗**矢量图标**（用户 11-08：「还是好丑……你知道我喜欢矢量图标来代替信息的」）。
 *
 * 一件事说清：**图标只说"这是哪一类"，名字仍然在**——屏上要能读懂，不许靠记忆认图形。
 * 图标取仓里那套 Tabler（`@/lib/icons`），与设置侧栏每一项（`Layers3`／`Globe`／`Package`…）同一族；
 * 尺寸走 `iconSize.sm`（14px），与组头那行 12px 的 caption 同一量级。
 *
 * 类别是闭集（后端 `registry.SOURCE_CATEGORIES` 七个值）。多出来的类别**不画图标**——
 * 不拿一枚兜底图标糊上去：那一格本来就是"没见过的东西"，画个假的比空着更糟。
 */

import { Box, FileText, Globe, Mail, Monitor, Package, Rss } from '@/lib/icons'

const CATEGORY_ICON = {
  /** 「装一个自己的」＝他自己写的那个包（屏上那句「Installing runs that package's own code」）。 */
  custom: Package,
  /** 设备类：机器上那一头。 */
  device: Monitor,
  /** 邮箱类。 */
  email: Mail,
  /** 订阅类（RSS 那一条轴）。 */
  feed: Rss,
  /** 文件类：本地文件导入。 */
  file: FileText,
  /** 网站类。 */
  site: Globe,
  /** 软件类：装在本机的那些程序。 */
  software: Box
} as const

export function categoryIcon(category: string) {
  return CATEGORY_ICON[category as keyof typeof CATEGORY_ICON] ?? null
}
