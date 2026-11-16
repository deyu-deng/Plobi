import { useStore } from '@nanostores/react'

import { useI18n } from '@/i18n'
import { $mindPendingCount, setMindPendingOpen } from '@/store/mind-pending'

import { TopBarIconButton } from './top-bar-icon-button'

/**
 * 对话卡抬头那一列里的「等你点头（写脑）」入口（`WP-MIND-CONSENT-EVENTS`）。
 *
 * 为什么搬到这里：原来是左栏底部状态栏那颗角标 ＋ 设置页一个按钮。用户 11-16 原话
 * 「这个记忆写入人点头的地方其实很奇怪。现在是放在左侧栏底部的一个图标里面是不是。
 * 我打算删掉这个图标」——**要点头的东西应当就在说话的那张卡上**。
 *
 * 形状不复用任何私货：`TopBarIconButton` 是那一列的唯一形态（角标、按下态、读屏标签
 * 都在它身上），加一条记录即可（`chat-top-bar.tsx` 的注册表）。计数由浮层那支轮询
 * 喂 store（5 分钟一次），这里只读，不自己再拉一遍。
 *
 * 图标用 `checklist` 而不是 `book`：`book` 已经是档案那一枚，两枚同图标等于让人猜。
 */
export function MindPendingButton() {
  const { t } = useI18n()
  const count = useStore($mindPendingCount)

  return (
    <TopBarIconButton
      count={count}
      icon="checklist"
      label={t.mindPending.title}
      onClick={() => setMindPendingOpen(true)}
    />
  )
}
