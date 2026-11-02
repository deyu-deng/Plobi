import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { toggleKeybindPanel } from '@/store/keybinds'

import { ListRow } from './primitives'

/**
 * 快捷键这一格原来住在左上角那条图标条里，11-02 拍「底部只留六枚」后搬进设置·外观。
 * 整行就是按钮（`ListRow` 的 `onActivate`）：这一屏的开关类用开关，跳出去的面板用整行，
 * 不再多造一句"打开…"的文案。面板本身也有 ⌘/ 那个键盘入口，两条路都通。
 */
export function KeybindsRow() {
  const { t } = useI18n()

  return (
    <ListRow
      onActivate={() => {
        triggerHaptic('open')
        toggleKeybindPanel()
      }}
      title={t.titlebar.openKeybinds}
    />
  )
}
