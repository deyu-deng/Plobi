import { atom } from 'nanostores'

import type { PreviewTarget } from './preview'

/**
 * 「在**工作区卡片**里开一个页签」的对外通道（WP-TWO-CARDS）。
 *
 * ## 为什么要有这一条
 *
 * 工作区卡片（`RightRailWorkbench`）本来就有完整页签机制，但它那一整套页签状态
 * （`openFiles`／`l1Tabs`／`l2Tabs`／`l1Active`／`l2Active`）住在
 * `app/desktop-controller.tsx` 的**组件内 state** 里 ⇒ 档案卡「相关笔记」那一格、
 * 右栏那一格这些**独立组件够不着它**。
 *
 * 于是它们只好绕道走另一条路：`notes-list.tsx::openNote` 调
 * `setCurrentSessionPreviewTarget` ＋ `setRightRailView(RIGHT_RAIL_VIEW_PREVIEW)`
 * ⇒ 笔记落进**对话旁那条预览轨**（用户 11-09 11:47：「点击打开笔记，还是不是在
 * 最右边那个有页签的卡片里面展示呢？」「以后只有两张卡片了」）。
 *
 * ## 这一刀怎么改
 *
 * 预览轨**删掉**（`ChatPreviewRail` 与 `app/chat/right-rail/preview*.tsx` 的实现），
 * 笔记/文件预览一律落到**工作区卡片的页签**里。为了让独立组件能开那个页签，
 * 这里放一条**请求通道**：调用方只管「我要开这个」，真正的页签写入仍由
 * `desktop-controller.tsx` 里那个 `openFileTab` 做（它知道当前是 L1 还是 L2 那条腿）。
 *
 * ⇒ 一条通道、两个写者各管各的：**这里**管「请求」，**那里**管「落页签」。
 */

/** 一次「在工作区卡片里开这个」的请求。 */
export interface WorkbenchFileOpenRequest {
  /** 页签标题（通常是文件名或笔记标题）。 */
  title: string
  /** 要预览的目标（本地路径 / url / 内容）。 */
  target: PreviewTarget
}

/**
 * 待处理的打开请求。`desktop-controller.tsx` 订阅它：收到就调自己的 `openFileTab`，
 * 然后把这里清空（清空在**写完页签之后**，所以不会丢请求）。
 */
export const $workbenchFileOpenRequest = atom<WorkbenchFileOpenRequest | null>(null)

/**
 * 请求在工作区卡片里开一个页签。
 *
 * 重复请求同一个目标不会堆多条：`desktop-controller.tsx` 侧按 `file:<key>` 归一。
 */
export function requestWorkbenchFileOpen(target: PreviewTarget, title: string): void {
  $workbenchFileOpenRequest.set({ target, title })
}
