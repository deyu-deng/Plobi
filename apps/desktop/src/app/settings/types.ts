import type { Dispatch, SetStateAction } from 'react'

import type { PlobiGateway } from '@/plobi'
import type { IconComponent } from '@/lib/icons'
import type { EnvVarInfo } from '@/types/plobi'

export type SettingsView =
  | 'about'
  | 'capabilities'
  | 'gateway'
  | 'keys'
  | 'network'
  | 'notifications'
  | 'offload'
  | 'providers'
  | 'sessions'
  | 'sources'
  | `config:${string}`
export type EnvPatch = Partial<Pick<EnvVarInfo, 'is_set' | 'redacted_value'>>

export interface SettingsPageProps {
  gateway?: PlobiGateway | null
  onClose: () => void
  onConfigSaved?: () => void
  onMainModelChanged?: (provider: string, model: string) => void
}

export interface ProviderGroup {
  name: string
  priority: number
  entries: [string, EnvVarInfo][]
  hasAnySet: boolean
}

/**
 * 一屏里的一个分组。有组名就有组标题可显示（`SettingsGroup` 的 `title`）。
 *
 * 分组只影响**怎么排**，不影响字段本身：`keys` 是这一组按屏上顺序排的键，
 * 一屏的 `keys` 必须是各组 `keys` 的并集（渲染器按组切 `visibleFields`）。
 */
export interface DesktopConfigGroup {
  /** i18n 键（`settings.groups.<屏>.<组>`），不是显示文案本身。 */
  titleKey: string
  keys: string[]
}

export interface DesktopConfigSection {
  id: string
  label: string
  icon: IconComponent
  keys: string[]
  /** 给了就按组渲染、每组一张带组名的卡；不给就整屏一张卡（现有行为）。 */
  groups?: DesktopConfigGroup[]
}

export interface EnvRowProps {
  varKey: string
  info: EnvVarInfo
  edits: Record<string, string>
  revealed: Record<string, string>
  saving: string | null
  setEdits: Dispatch<SetStateAction<Record<string, string>>>
  onSave: (key: string) => void
  onClear: (key: string) => void
  onReveal: (key: string) => void
  compact?: boolean
}
