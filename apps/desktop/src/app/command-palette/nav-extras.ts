import {
  Calendar,
  Clock,
  Cpu,
  type IconComponent,
  MessageCircle,
  NotebookTabs,
  Package,
  Pencil,
  Settings,
  Square,
  Starmap,
  Terminal,
  Wrench
} from '@/lib/icons'

/**
 * 「跳到某一处」那一列的**图标**（宿主留的那张表）。
 *
 * ⚠️ **图标仍归宿主**：命令面板那一列的图标是 **Lucide 组件**（声明里只有数据、没有组件）
 * ⇒ 宿主留一张「slot id → 图标」的表——**与设置屏那八段、命令中心那四段同一条规矩**
 * （`settings/constants.ts` 的 `SECTION_ICONS`、命令中心自己那张表）。
 *
 * 插件加的那一格没有宿主图标 ⇒ 用宿主给的那枚**通用入口图标**（形状是宿主的；插件自带
 * 组件是「深」的那一步：插件带前端 bundle）。
 */
export const NAV_ICONS: Record<string, IconComponent> = {
  skills: Wrench,
  messaging: MessageCircle,
  artifacts: Package,
  knowledge: NotebookTabs,
  diary: Pencil,
  agenda: Calendar,
  cron: Clock,
  'command-center': Terminal,
  settings: Settings,
  agents: Cpu,
  starmap: Starmap
}

/** 插件加的那一格没有宿主图标 ⇒ 用这一枚。 */
export const NAV_FALLBACK_ICON: IconComponent = Square
