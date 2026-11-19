import { MonitorEntryButton } from './monitor-entry-button'
import { useMonitorFace } from './monitor-faces'

/**
 * 对话卡抬头那一列里的「等你点头（写脑）」**入口**（`WP-MIND-CONSENT-EVENTS`）。
 *
 * 为什么搬到这里：原来是左栏底部状态栏那颗角标 ＋ 设置页一个按钮。用户 11-16 原话
 * 「这个记忆写入人点头的地方其实很奇怪。现在是放在左侧栏底部的一个图标里面是不是。
 * 我打算删掉这个图标」——**要点头的东西应当就在说话的那张卡上**。
 *
 * **11-19 17:0x 统一成那一张卡**：这一枚现在跟档案、待放行**同一个组件**
 * （`monitor-entry-button.tsx`）：同形状、同角标、同按下态、同一点击行为。
 * 点它 ⇒ 那一张卡切到「写脑待批」那一面；再点一下 ⇒ 关掉。
 *
 * 图标用 `checklist` 而不是 `book`：`book` 已经是档案那一枚，两枚同图标等于让人猜。
 */
export function MindPendingButton() {
  const face = useMonitorFace('mind-pending')

  return <MonitorEntryButton face={face} />
}
