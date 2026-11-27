import { useMemo } from 'react'

import { settingsSectionsFor } from '@/app/shell/slot-registry'
import { useSlotRegistry } from '@/app/shell/slot-registry-context'
import type { IconComponent } from '@/lib/icons'
import { SlidersHorizontal } from '@/lib/icons'

import { SECTION_ICONS } from './constants'

/**
 * 设置屏的**那几段**（用户 11-25 23:3x：一段＝一组配置字段；设置屏＝**第四个位置**）。
 *
 * **清单从 slot 声明表派生**（`app/shell/slot-registry.ts` 的 `settings`／`sections` 区）——
 * 出厂那八段在那份声明里，插件加的那一段**自动出现**（用户关掉的那一段**真的不在**）。
 *
 * **图标仍归宿主**：那八段的图标是 **Lucide 组件**（声明里只有数据、没有组件）⇒
 * 宿主留一张「段 id → 图标」的表（`constants.ts` 的 `SECTION_ICONS`）；插件加的那一段
 * 没有宿主图标 ⇒ 用宿主给的那枚**通用设置图标**（形状是宿主的，插件不自带组件——那是
 * 「深」的那一步：插件带前端 bundle）。
 */
export interface SettingsSection {
  /** 这一段的 id（`config:<id>` 那个寻址用它）。 */
  id: string
  /** 屏上写的名字（出厂那几段走 i18n；插件那一段用自己声明的）。 */
  label: string
  /** 这一段显示哪几个配置字段。 */
  keys?: string[]
  /** 这一段显示应用的哪一页（插件加的那一段走它）。 */
  view?: string
  /** 这一段的分组（宿主的形状契约：这一段怎么分块）。 */
  groups?: { titleKey: string; keys: string[] }[]
  /** 这一段的图标（宿主的：那八段各自的，插件那一段是通用的）。 */
  icon: IconComponent
}

export function useSettingsSections(): SettingsSection[] {
  const registry = useSlotRegistry()

  return useMemo(
    () =>
      settingsSectionsFor(registry).map(section => ({
        ...section,
        icon: SECTION_ICONS[section.id] ?? SlidersHorizontal
      })),
    [registry]
  )
}
