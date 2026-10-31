import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { $hapticsMuted, toggleHapticsMuted } from '@/store/haptics'
import { toggleKeybindPanel } from '@/store/keybinds'

import type { TitlebarTool } from './titlebar-controls'

/**
 * 设置 / 快捷键 / 触感反馈这三枚。卡片模式里它们从顶栏挪到左下图标条
 * （用户 10-30：「也做成图标挪下来」），顶栏与图标条共用这一份，不许各写一套。
 */
export function useSystemTools(onOpenSettings: () => void): TitlebarTool[] {
  const { t } = useI18n()
  const hapticsMuted = useStore($hapticsMuted)

  return useMemo(() => {
    const toggleHaptics = () => {
      if (!hapticsMuted) {
        triggerHaptic('tap')
      }

      toggleHapticsMuted()

      if (hapticsMuted) {
        window.requestAnimationFrame(() => triggerHaptic('success'))
      }
    }

    return [
      {
        active: hapticsMuted,
        icon: <Codicon name={hapticsMuted ? 'mute' : 'unmute'} />,
        id: 'haptics',
        label: hapticsMuted ? t.titlebar.unmuteHaptics : t.titlebar.muteHaptics,
        onSelect: toggleHaptics
      },
      {
        icon: <Codicon name="keyboard" />,
        id: 'keybinds',
        label: t.titlebar.openKeybinds,
        onSelect: () => {
          triggerHaptic('open')
          toggleKeybindPanel()
        }
      },
      {
        icon: <Codicon name="settings-gear" />,
        id: 'settings',
        label: t.titlebar.openSettings,
        onSelect: () => {
          triggerHaptic('open')
          onOpenSettings()
        }
      }
    ]
  }, [hapticsMuted, onOpenSettings, t])
}
