import { useStore } from '@nanostores/react'

import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { $hapticsMuted, setHapticsMuted } from '@/store/haptics'

import { ListRow } from './primitives'

/**
 * 触感反馈这一格从左上角那条图标条搬进设置（用户 11-02 拍「底部只留六枚」）。
 *
 * 开关按的是**静音**那一头，因为标题就是目录里那句「关闭触感反馈」：目录没有
 * "触感反馈"这个名词，宁可让开关的方向跟着这句现成的人话走，也不为一格去动
 * 五份语言目录（那一族此刻正压在别人手上）。
 *
 * 取消静音的那一下当场震一次——不然没人知道这格管的是哪件事。
 */
export function HapticsRow() {
  const { t } = useI18n()
  const muted = useStore($hapticsMuted)

  return (
    <ListRow
      action={
        <Switch
          checked={muted}
          onCheckedChange={value => {
            setHapticsMuted(value)

            if (!value) {
              triggerHaptic('success')
            }
          }}
        />
      }
      title={t.titlebar.muteHaptics}
    />
  )
}
