import { useStore } from '@nanostores/react'
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { COMPLETION_SOUND_VARIANTS, previewCompletionSound } from '@/lib/completion-sound'
import { triggerHaptic } from '@/lib/haptics'
import { Bell, Play } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $completionSoundVariantId, setCompletionSoundVariantId } from '@/store/completion-sound'
import {
  $nativeNotifyPrefs,
  NATIVE_NOTIFICATION_KINDS,
  sendTestNativeNotification,
  setNativeNotifyEnabled,
  setNativeNotifyKind
} from '@/store/native-notifications'
import { notify } from '@/store/notifications'

import { CONTROL_TEXT } from './constants'
import { HapticsRow } from './haptics-row'
import { ListRow, SettingsContent, SettingsGroup } from './primitives'

const CAPTION = 'text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)'

function Caption({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn(CAPTION, className)}>{children}</p>
}

function ToggleRow(props: {
  checked: boolean
  description: string
  disabled?: boolean
  label: string
  onChange: (on: boolean) => void
}) {
  return (
    <ListRow
      action={
        <Switch
          aria-label={props.label}
          checked={props.checked}
          disabled={props.disabled}
          onCheckedChange={on => {
            triggerHaptic('selection')
            props.onChange(on)
          }}
        />
      }
      description={props.description}
      title={props.label}
    />
  )
}

export function NotificationsSettings() {
  const { t } = useI18n()
  const prefs = useStore($nativeNotifyPrefs)
  const completionSoundVariantId = useStore($completionSoundVariantId)
  const copy = t.settings.notifications

  const runTest = async () => {
    triggerHaptic('open')
    const ok = await sendTestNativeNotification(copy.testTitle, copy.testBody)
    notify({ kind: ok ? 'info' : 'error', message: ok ? copy.testSent : copy.testUnsupported })
  }

  return (
    <SettingsContent>
      {/* 一组一张卡（用户 11-02 拍「外壳 + 设置」，形状照 Qoder 实测）：抬头那一行是
          组名 + 一句说明，卡里行与行之间虚线分隔，末行不带。 */}
      <SettingsGroup description={copy.intro} title={copy.title}>
        <ToggleRow
          checked={prefs.enabled}
          description={copy.enableAllDesc}
          label={copy.enableAll}
          onChange={setNativeNotifyEnabled}
        />

        {NATIVE_NOTIFICATION_KINDS.map(kind => (
          <ToggleRow
            checked={prefs.enabled && prefs.kinds[kind]}
            description={copy.kinds[kind].description}
            disabled={!prefs.enabled}
            key={kind}
            label={copy.kinds[kind].label}
            onChange={on => setNativeNotifyKind(kind, on)}
          />
        ))}

        <ListRow
          action={
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Select
                onValueChange={value => {
                  const variantId = Number.parseInt(value, 10)

                  setCompletionSoundVariantId(variantId)
                  previewCompletionSound(variantId)
                  triggerHaptic('selection')
                }}
                value={String(completionSoundVariantId)}
              >
                <SelectTrigger className={cn('min-w-56', CONTROL_TEXT)}>
                  <SelectValue />
                </SelectTrigger>

                <SelectContent>
                  {COMPLETION_SOUND_VARIANTS.map(variant => (
                    <SelectItem key={variant.id} value={String(variant.id)}>
                      {variant.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Button
                className="gap-1.5"
                onClick={() => {
                  previewCompletionSound()
                  triggerHaptic('crisp')
                }}
                size="sm"
                type="button"
                variant="outline"
              >
                <Play className="size-3.5" />
                {copy.completionSoundPreview}
              </Button>
            </div>
          }
          description={copy.completionSoundDesc}
          title={copy.completionSoundTitle}
        />

        {/* 触感这一格原来住在左上角那条图标条里，11-02 拍「底部只留六枚」后搬到这里；
            组件与它的判据在 haptics-row.tsx，这一屏只负责挂。 */}
        <HapticsRow />

        <div className="flex flex-col gap-2 py-[var(--dl-row-pad-y)]">
          <Button className="self-start" onClick={() => void runTest()} size="sm" type="button" variant="outline">
            <Bell />
            {copy.test}
          </Button>
          <Caption>{copy.focusedHint}</Caption>
        </div>
      </SettingsGroup>
    </SettingsContent>
  )
}
