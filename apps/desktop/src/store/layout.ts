import { atom, computed, type ReadableAtom } from 'nanostores'

import { Codecs, persistentAtom } from '@/lib/persisted'
import { arraysEqual, insertUniqueId } from '@/lib/storage'

// 「写脑待批那一面开着没有」这枚旗标住在 `store/mind-pending.ts`（那是那一族的另一条线
// 立的文件，本刀不改它）⇒ 这里**只调用、不改它**：切面时顺手把它拨到一致，
// 免得两枚旗标各说各话。
import { setMindPendingOpen } from './mind-pending'
import { $paneStates, ensurePaneRegistered, setPaneOpen, setPaneWidthOverride, togglePane } from './panes'

// 主界面「卡片壳」：渐变底 + 对话/右栏两张白卡，左栏不上卡（用户 10-30 定）。
// 存成偏好而不是写死：他不喜欢时能关回铺满的旧样子。
export const SHELL_CARDS_STORAGE_KEY = 'plobi.desktop.shellCards'
export const $shellCards = persistentAtom(SHELL_CARDS_STORAGE_KEY, true, Codecs.bool)

export function setShellCards(value: boolean): void {
  $shellCards.set(Boolean(value))
}

// 对话卡 ↔ 右栏卡互换（用户 10-31：拖一张卡到另一张上即换位）。持久化，
// 因为这是"你把工作台摆在哪边"的决定，不该重开一次就忘掉。
export const SHELL_SWAPPED_STORAGE_KEY = 'plobi.desktop.shellSwapped'
export const $shellSwapped = persistentAtom(SHELL_SWAPPED_STORAGE_KEY, false, Codecs.bool)

export function setShellSwapped(value: boolean): void {
  $shellSwapped.set(Boolean(value))
}

export function toggleShellSwapped(): void {
  $shellSwapped.set(!$shellSwapped.get())
}

export const SIDEBAR_DEFAULT_WIDTH = 186
export const SIDEBAR_MAX_WIDTH = 300
// Open at the same width as the sessions sidebar so the two rails match, but
// allow shrinking well below that (~30% under the old 14rem floor) for users who
// want a narrow tree.
export const FILE_BROWSER_DEFAULT_WIDTH = `${SIDEBAR_DEFAULT_WIDTH}px`
export const FILE_BROWSER_MIN_WIDTH = '10rem'
export const FILE_BROWSER_MAX_WIDTH = '20rem'

export const SIDEBAR_SESSIONS_PAGE_SIZE = 50

const SIDEBAR_PINNED_STORAGE_KEY = 'plobi.desktop.pinnedSessions'
const SIDEBAR_AGENTS_GROUPED_STORAGE_KEY = 'plobi.desktop.agentsGroupedByWorkspace'
const SIDEBAR_CRON_OPEN_STORAGE_KEY = 'plobi.desktop.sidebarCronOpen'
const SIDEBAR_MESSAGING_OPEN_STORAGE_KEY = 'plobi.desktop.sidebarMessagingOpen'
const SIDEBAR_SESSION_ORDER_STORAGE_KEY = 'plobi.desktop.sessionOrder'
const SIDEBAR_SESSION_ORDER_MANUAL_STORAGE_KEY = 'plobi.desktop.sessionOrder.manual'
const SIDEBAR_WORKSPACE_ORDER_STORAGE_KEY = 'plobi.desktop.workspaceOrder'
const SIDEBAR_WORKSPACE_PARENT_ORDER_STORAGE_KEY = 'plobi.desktop.workspaceParentOrder'
const SIDEBAR_PROJECT_ORDER_STORAGE_KEY = 'plobi.desktop.projectOrder'
const SIDEBAR_WORKSPACE_COLLAPSED_STORAGE_KEY = 'plobi.desktop.workspaceCollapsed'
const SIDEBAR_AGENT_CATEGORY_COLLAPSED_STORAGE_KEY = 'plobi.desktop.agentCategoriesCollapsed'
const SIDEBAR_DISMISSED_AUTO_PROJECTS_STORAGE_KEY = 'plobi.desktop.dismissedAutoProjects'
const SIDEBAR_DISMISSED_WORKTREES_STORAGE_KEY = 'plobi.desktop.dismissedWorktrees'
const PANES_FLIPPED_STORAGE_KEY = 'plobi.desktop.panesFlipped'

export const CHAT_SIDEBAR_PANE_ID = 'chat-sidebar'
export const FILE_BROWSER_PANE_ID = 'file-browser'

ensurePaneRegistered(CHAT_SIDEBAR_PANE_ID, { open: true })
ensurePaneRegistered(FILE_BROWSER_PANE_ID, { open: false })

// 收起 = 40px 图标条，不再归零（用户 10-31）。Pane 永远开着；「展开/收起」只是
// 这条栏的形态开关，所以不再跟 pane 的 open 状态绑在一起。
export const SIDEBAR_COLLAPSED_WIDTH = 40
const SIDEBAR_EXPANDED_STORAGE_KEY = 'plobi.desktop.sidebarExpanded'
export const $sidebarExpanded = persistentAtom(SIDEBAR_EXPANDED_STORAGE_KEY, true, Codecs.bool)
export const $sidebarOpen: ReadableAtom<boolean> = $sidebarExpanded

export const $fileBrowserOpen: ReadableAtom<boolean> = computed(
  $paneStates,
  states => states[FILE_BROWSER_PANE_ID]?.open ?? false
)

export const $sidebarWidth: ReadableAtom<number> = computed($paneStates, states => {
  const override = states[CHAT_SIDEBAR_PANE_ID]?.widthOverride

  return typeof override === 'number' ? override : SIDEBAR_DEFAULT_WIDTH
})

export const $pinnedSessionIds = persistentAtom(SIDEBAR_PINNED_STORAGE_KEY, [] as string[], Codecs.stringArray)
export const $sidebarSessionOrderIds = persistentAtom(
  SIDEBAR_SESSION_ORDER_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
export const $sidebarSessionOrderManual = persistentAtom(SIDEBAR_SESSION_ORDER_MANUAL_STORAGE_KEY, false, Codecs.bool)
export const $sidebarWorkspaceOrderIds = persistentAtom(
  SIDEBAR_WORKSPACE_ORDER_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
// Order of the top-level repo "parent" groups in the worktree tree (worktrees
// within a parent reuse $sidebarWorkspaceOrderIds).
export const $sidebarWorkspaceParentOrderIds = persistentAtom(
  SIDEBAR_WORKSPACE_PARENT_ORDER_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
// Manual drag-order of projects in the overview. Empty = the deterministic
// default sort (active first, explicit before auto, by recency); once the user
// drags a project their order wins (orderByIds surfaces new projects on top).
export const $sidebarProjectOrderIds = persistentAtom(
  SIDEBAR_PROJECT_ORDER_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
// Repo/worktree nodes that the user has explicitly COLLAPSED. Absent = open, so
// a project's folders auto-open when you enter it (and persist your collapses
// across reloads). Keyed by stable node id (repo root / worktree path).
export const $sidebarWorkspaceCollapsedIds = persistentAtom(
  SIDEBAR_WORKSPACE_COLLAPSED_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
// Agent-category columns (项目 / 课程 / 管家 / 事件 / 研究) collapse the same way
// workspaces do: open by default, and only the ones the user actually folded away
// are remembered. An agent row's fold state living in component `useState` made this
// column the odd one out — every other sidebar section keeps it in the store.
export const $sidebarAgentCategoryCollapsedIds = persistentAtom(
  SIDEBAR_AGENT_CATEGORY_COLLAPSED_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
// Auto-derived (git-repo) projects the user has dismissed ("deleted") from the
// overview. Keyed by repo-root path; persisted so they stay hidden. Explicit
// projects are deleted for real instead — this only declutters the auto tier.
export const $dismissedAutoProjectIds = persistentAtom(
  SIDEBAR_DISMISSED_AUTO_PROJECTS_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
// Worktree rows removed from the UI after a `git worktree remove`. The on-disk
// dir is gone but historical sessions still reference its path, so we hide the
// row by id (worktree path) to keep "remove" feeling real.
export const $dismissedWorktreeIds = persistentAtom(
  SIDEBAR_DISMISSED_WORKTREES_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
export const $sidebarPinsOpen = atom(true)
// Set by the PaneShell hover-reveal overlay while the sidebar is collapsed; kept
// true the whole time it's a floating overlay (not just while shown) so the
// consumer mounts contents off-screen, ready to slide. ChatSidebar mounts its
// rows on `sidebarOpen || this`.
export const $sidebarOverlayMounted = atom(false)
export const $sidebarRecentsOpen = atom(true)
// Cron-job sessions live in their own section below recents, collapsed by
// default (it only renders at all when cron sessions exist) so the
// scheduler's `[IMPORTANT: …]` first-message previews don't spam recents.
export const $sidebarCronOpen = persistentAtom(SIDEBAR_CRON_OPEN_STORAGE_KEY, false, Codecs.bool)
// Messaging platform sections collapse by default (they can be numerous and
// tall). We persist the ids the user has *explicitly expanded*, so the default
// stays collapsed unless they've opened a platform before.
export const $sidebarMessagingOpenIds = persistentAtom(
  SIDEBAR_MESSAGING_OPEN_STORAGE_KEY,
  [] as string[],
  Codecs.stringArray
)
export const $sidebarAgentsGrouped = persistentAtom(SIDEBAR_AGENTS_GROUPED_STORAGE_KEY, false, Codecs.bool)
// When true, the sessions sidebar moves to the right and the file browser +
// preview rail move to the left — a mirror of the default layout.
export const $panesFlipped = persistentAtom(PANES_FLIPPED_STORAGE_KEY, false, Codecs.bool)
export const $isSidebarResizing = atom(false)
export const $sessionsLimit = atom(SIDEBAR_SESSIONS_PAGE_SIZE)

// Toggle a repo/worktree node's persisted collapse state (absent = open).
export function toggleWorkspaceNodeCollapsed(id: string): void {
  const current = $sidebarWorkspaceCollapsedIds.get()

  $sidebarWorkspaceCollapsedIds.set(current.includes(id) ? current.filter(nodeId => nodeId !== id) : [...current, id])
}

export function toggleSidebarAgentCategoryCollapsed(category: string): void {
  const current = $sidebarAgentCategoryCollapsedIds.get()

  $sidebarAgentCategoryCollapsedIds.set(
    current.includes(category) ? current.filter(id => id !== category) : [...current, category]
  )
}

// Dismiss ("delete") an auto-derived project from the overview.
export function dismissAutoProject(id: string): void {
  const current = $dismissedAutoProjectIds.get()

  if (!current.includes(id)) {
    $dismissedAutoProjectIds.set([...current, id])
  }
}

// Hide a worktree row after it's been removed via git.
export function dismissWorktree(id: string): void {
  const current = $dismissedWorktreeIds.get()

  if (!current.includes(id)) {
    $dismissedWorktreeIds.set([...current, id])
  }
}

// A hidden worktree becomes visible again as soon as the user explicitly starts
// or opens work there (for example, selecting an already-checked-out branch).
export function restoreWorktree(id: string): void {
  const current = $dismissedWorktreeIds.get()

  if (current.includes(id)) {
    $dismissedWorktreeIds.set(current.filter(worktreeId => worktreeId !== id))
  }
}

export function setSidebarWidth(width: number) {
  const bounded = Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_DEFAULT_WIDTH, width))
  setPaneWidthOverride(CHAT_SIDEBAR_PANE_ID, bounded)
}

export function setSidebarOpen(open: boolean) {
  $sidebarExpanded.set(Boolean(open))
  markSidebarAnimating()
}

export function toggleSidebarOpen() {
  $sidebarExpanded.set(!$sidebarExpanded.get())
  markSidebarAnimating()
}

export function toggleFileBrowserOpen() {
  togglePane(FILE_BROWSER_PANE_ID)
}

export function setFileBrowserOpen(open: boolean) {
  setPaneOpen(FILE_BROWSER_PANE_ID, open)
}

// "Reveal this file in the file-browser tree" — an absolute path the tree
// subscribes to, expanding ancestor folders and selecting/scrolling to it. Reset
// to null by the tree once consumed.
export const $revealInTreeRequest = atom<null | string>(null)

export function revealFileInTree(path: string): void {
  setFileBrowserOpen(true)
  $revealInTreeRequest.set(path)
}

// Hotkey → focus the sessions search field. Opens the sidebar first, then lets
// the field (which only mounts when the sidebar is open) subscribe + focus.
export const SESSION_SEARCH_FOCUS_EVENT = 'plobi:focus-session-search'

export function requestSessionSearchFocus() {
  setSidebarOpen(true)

  if (typeof window !== 'undefined') {
    window.setTimeout(() => window.dispatchEvent(new CustomEvent(SESSION_SEARCH_FOCUS_EVENT)), 0)
  }
}

export function togglePanesFlipped() {
  $panesFlipped.set(!$panesFlipped.get())
}

export function setPanesFlipped(value: boolean) {
  $panesFlipped.set(Boolean(value))
}

// 「只显示这一张卡」（用户 10-31）：聚焦对话或工作面，另一张收起来。
// 不持久化——这是一次性动作，重开回到三栏。
export type CardFocus = 'chat' | 'workbench' | null
export const $cardFocus = atom<CardFocus>(null)

/**
 * 「只显示这一张卡」那 200ms：只在这时给网格列加过渡，拖拽改宽时不能有延迟。
 *
 * 与左栏那把尺（`$sidebarAnimating`）**同一个形状、两件事互不串门**：
 * 卡片放大/还原时列数不变（那两条轨只是被收成 0 宽），所以
 * `grid-template-columns` 可以从一条 px 轨道过渡到另一条 px 轨道——
 * 列**个数**一变，浏览器无从插值，就只能硬跳（11-10 现场：最大化一点动静都没有）。
 */
export const $cardFocusAnimating = atom(false)
let cardFocusAnimationTimer: ReturnType<typeof setTimeout> | null = null

function markCardFocusAnimating(): void {
  $cardFocusAnimating.set(true)

  if (cardFocusAnimationTimer) {
    clearTimeout(cardFocusAnimationTimer)
  }

  cardFocusAnimationTimer = setTimeout(() => {
    $cardFocusAnimating.set(false)
    cardFocusAnimationTimer = null
  }, 240)
}

export function setCardFocus(next: CardFocus): void {
  markCardFocusAnimating()
  $cardFocus.set(next)
}

// ── 这一族（档案／待放行／写脑待批）的**那一张卡** ───────────────────────────
//
// 用户 11-19 17:0x：「**完全复用同样的组件。不要写三套代码。只是容器内部的信息和UI细节不同而已。**」
// 上一刀（`WP-QODER-MONITOR-SPEC`）统一了**长相**，这一刀统一**交互**：
// 这一族从「档案卡」变成「这一族」——显隐、当前面、钉住、浮层位置都是这一族的事。
//
// 显隐口径照旧（用户 11-08 21:1x）：`auto` 跟着对话卡全屏走（默认那档），
// `open`／`closed` 是用户点过的结果。

/**
 * 这一族有几面：档案、待放行、写脑待批（顺序＝抬头右上角那几枚的顺序）。
 *
 * **身份是开放的**（`string`）：那一列从 **slot 声明表**派生（裁定 239 第四刀），
 * 插件可以带来它自己的一枚 ⇒ 宿主不预先知道有哪些 id。
 */
export type MonitorFaceId = string

/**
 * 显隐口径。**不是容器形态** —— 形态是 `'column'／'float'`，在 `app/shell/monitor-panel.tsx`。
 *
 * 三档而不是一个布尔：默认那档要**跟着对话卡全屏与否走**，用户点过之后才固定。
 */
export type MonitorSlotMode = 'auto' | 'open' | 'closed'

export const $monitorSlotMode = atom<MonitorSlotMode>('auto')

/**
 * 这一族的卡此刻该不该露脸：`auto` 跟着对话卡全屏（Qoder 的默认形态），
 * `open`／`closed` 是用户点过的结果。
 *
 * 回调是**按参数展开**拿值的（nanostores 1.x 的 `computed([a, b], cb)` 调的是 `cb(a, b)`，
 * 不是 `cb([a, b])`）——写成数组解构会把 `'auto'` 这个字符串切成一两个字，
 * 三档全不匹配，于是这张卡**恒为不露脸**（11-09 现场：点图标开不了、全屏也不默认显示）。
 */
export const $monitorSlotVisible = computed([$monitorSlotMode, $cardFocus], (mode, focus) => {
  if (mode === 'open') {
    return true
  }

  if (mode === 'closed') {
    return false
  }

  return focus === 'chat'
})

export function setMonitorSlotMode(mode: MonitorSlotMode): void {
  $monitorSlotMode.set(mode)
}

/** 当前显示哪一面。默认是档案（这一族的主面）。 */
export const $monitorFace = atom<MonitorFaceId>('dossier')

export function setMonitorFace(face: MonitorFaceId): void {
  $monitorFace.set(face)
}

/**
 * 三枚入口**同一个点击行为**（用户 11-19 17:0x）：
 * **点哪一枚 ⇒ 那一张卡显示那一面；再点同一枚 ⇒ 关掉；点另一枚 ⇒ 换面。**
 *
 * 窄形态（对话卡装不下那一列）另说：那张卡折起来了 ⇒ 入口展开的是**贴头浮层**，
 * 面的切换与开合同样走这一条。
 */
export function toggleMonitorFace(face: MonitorFaceId): void {
  if ($chatCardNarrow.get()) {
    const same = $monitorPeekOpen.get() && $monitorFace.get() === face

    $monitorPeekOpen.set(!same)

    if (!same) {
      $monitorFace.set(face)
      setMindPendingOpen(face === 'mind-pending')
    }

    return
  }

  const same = $monitorSlotVisible.get() && $monitorFace.get() === face

  $monitorFace.set(face)
  $monitorSlotMode.set(same ? 'closed' : 'open')
  setMindPendingOpen(face === 'mind-pending' && !same)
}

/**
 * 从别处打开某一面（设置页那一行就是走这条）：切到那一面并展开。
 *
 * 窄形态下展开的是贴头浮层 —— 否则点了什么也看不见（那张卡折起来了）。
 */
export function openMonitorFace(face: MonitorFaceId): void {
  $monitorFace.set(face)

  if ($chatCardNarrow.get()) {
    $monitorPeekOpen.set(true)
  } else {
    $monitorSlotMode.set('open')
  }

  setMindPendingOpen(face === 'mind-pending')
}

// 「默认」只在这一面算数（用户 11-09：「全屏也不默认显示」）。
// 点过的那一次——开或关——只在这一面算数：换了面（对话卡全屏 ⇄ 工作区）就回到 `auto`，
// 否则关过一次之后，全屏也永远不显示了。对话卡全屏 ⇒ 默认显示；工作区 ⇒ 默认收起。
$cardFocus.listen(() => {
  $monitorSlotMode.set('auto')
})

/**
 * 这一族那张卡的形态：**并排一列**（对话右侧，占位）还是**浮层**（压着对话右上角）。
 *
 * 默认**并排**（Qoder 的实证，11-09 11:5x 从 asar 取的活证据）：Qoder 的
 * `taskMonitorMode` 偏好默认是 `"fix"`——「固定在任务右侧」，`"floating"` 是**用户
 * 可配的另一种偏好**，不是默认形态；会话内的监控面板本体是 280px 的 **flex 兄弟列**
 * （区域 `CHAT_SESSION_B1_2_REGION`），**不是 absolute 浮层**，所以它从不对话。
 *
 * 11-09 11:47 用户判：「在对话区单独展开的时候不是说了档案要默认显示吗？**而且不要
 * 遮挡对话区**」⇒ 浮层（absolute 压着对话）不该是默认；对话区单独展开时档案**默认在**，
 * 且**并排**（对话窄一点，但不遮挡）。
 */
export const $monitorPinned = atom(true)

export function toggleMonitorPin(): void {
  $monitorPinned.set(!$monitorPinned.get())
}

/**
 * 对话卡**容器**的宽度（px）—— 这一刀唯一的宽度判据。
 *
 * Qoder 的机制（11-08 逆向报告）：**形状由宽度决定，状态变量只管「在不在」**。
 * 它的任务监控卡在 `@container new-chat-b1 (max-width:639.98px)` 下整块收起，
 * 宽到 ≥924px 才从一列变两列。我们抄这一条：对话卡窄到装不下档案列时，
 * **档案卡折叠**（不占位、不遮挡），入口只剩抬头右上角那枚图标。
 *
 * 为什么用 JS 量而不是 CSS `@container`：① 档案卡是对话卡的**兄弟**，
 * CSS 容器查询管不到兄弟；② 我们还要按宽度改**点击行为**（窄时点图标弹贴头浮层、
 * 宽时点图标切换显隐），纯 CSS 做不到。
 *
 * **量的是对话卡整卡**，不是对话区：量对话区会自激——钉住形态下档案列占 22rem，
 * 对话区一变窄就判「窄」→ 折叠档案列 → 对话区变宽 → 又判「不窄」⇒ 来回抖。
 */
export const $chatCardWidth = atom(0)

/** 窄到这条线以下，档案卡折叠。768 ＝ 档案列 22rem ＋ 对话最少能读的宽度。 */
export const CHAT_CARD_NARROW_WIDTH = 768

export const $chatCardNarrow = computed($chatCardWidth, width => width > 0 && width < CHAT_CARD_NARROW_WIDTH)

export function setChatCardWidth(width: number): void {
  $chatCardWidth.set(Math.max(0, Math.round(width)))
}

/**
 * 窄形态下那个**贴头浮层**开没开（用户 11-16 19:4x 拍板：宽度不够就折叠、不遮对话）。
 *
 * 与 `$monitorSlotMode` 是两件事：那张管「这一族的卡在不在」，这个管「窄屏下浮层开没开」。
 * 宽了自动收起 —— 宽形态有自己的并排列／浮层，窄形态那个入口不该跟过去。
 */
export const $monitorPeekOpen = atom(false)

export function toggleMonitorPeek(): void {
  $monitorPeekOpen.set(!$monitorPeekOpen.get())
}

export function closeMonitorPeek(): void {
  $monitorPeekOpen.set(false)
}

$chatCardNarrow.listen(narrow => {
  if (!narrow) {
    $monitorPeekOpen.set(false)
  }
})

// ── 这一族那张卡的**逐段折叠**（Qoder 那一层，11-18 10:0x 拍板 B）────────────────
//
// Qoder 的任务监控面板有 13 个分区，**每个都能单独收起**，折叠时最多露 5 条
// （`TASK_MONITOR_MAX_COLLAPSED_ITEMS=5`），状态存在
// `qoder:workbench:task-monitor-section-visibility`。我们照抄**这一层机制**：
// 每一段都能折起来，折叠状态跟着这张卡走。
export const MONITOR_SECTION_IDS = ['summary', 'stages', 'progress', 'notes'] as const

export type MonitorSectionId = (typeof MONITOR_SECTION_IDS)[number]

export const $monitorCollapsedSections = persistentAtom<Record<string, boolean>>(
  'plobi.desktop.dossierCollapsedSections',
  {},
  Codecs.json<Record<string, boolean>>(value =>
    value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, boolean>) : {}
  )
)

export function isMonitorSectionCollapsed(id: string): boolean {
  return $monitorCollapsedSections.get()[id] === true
}

/** 折起／展开一段。折起时只留小标题。 */
export function toggleMonitorSection(id: string): void {
  const current = $monitorCollapsedSections.get()
  const next = { ...current, [id]: !current[id] }

  // 全展开时把记录清空（不留一份全 false 的垃圾）。
  $monitorCollapsedSections.set(Object.values(next).some(Boolean) ? next : {})
}

// ── 这一族那张卡**浮层的位置**（Qoder 那样可拖动并记住，11-18 10:0x）─────────────
//
// Qoder 的浮层是 `fixed z-[1000] w-[280px]`，头部 `h-9` 且 `cursor-grab`，
// 位置由 `clampTaskMonitorPosition` 夹在窗口内（`TASK_MONITOR_MARGIN=16`）。
// 我们照抄：**拖得动、拖完记住**，下次开还在原处。
export interface MonitorFloatPosition {
  x: number
  y: number
}

// **存储键保持原样**（`plobi.desktop.dossierFloatPosition`）：这一族只是把「档案卡的位置」
// 接管成「这一族那张卡的位置」—— 那个键装的是**位置**，不是名字；改了键＝把用户
// 已经摆好的位置作废。
export const $monitorFloatPosition = persistentAtom<MonitorFloatPosition | null>(
  'plobi.desktop.dossierFloatPosition',
  null,
  Codecs.json<MonitorFloatPosition | null>(value => {
    if (!value || typeof value !== 'object') {
      return null
    }

    const { x, y } = value as { x?: unknown; y?: unknown }

    return typeof x === 'number' && typeof y === 'number' ? { x, y } : null
  })
)

export function setMonitorFloatPosition(position: MonitorFloatPosition | null): void {
  $monitorFloatPosition.set(position)
}

export function toggleCardFocus(which: 'chat' | 'workbench'): void {
  $cardFocus.set($cardFocus.get() === which ? null : which)
}

/** 兼容旧名：true = 只留工作面。 */
export const $workbenchOnly = computed($cardFocus, focus => focus === 'workbench')

export function toggleWorkbenchOnly(): void {
  toggleCardFocus('workbench')
}

export function setWorkbenchOnly(value: boolean): void {
  setCardFocus(value ? 'workbench' : null)
}

// 左栏展开/收起的那 200ms：只在这时给网格列加过渡，拖拽改宽时不能有延迟。
export const $sidebarAnimating = atom(false)
let sidebarAnimationTimer: ReturnType<typeof setTimeout> | null = null

function markSidebarAnimating(): void {
  $sidebarAnimating.set(true)

  if (sidebarAnimationTimer) {
    clearTimeout(sidebarAnimationTimer)
  }

  sidebarAnimationTimer = setTimeout(() => {
    $sidebarAnimating.set(false)
    sidebarAnimationTimer = null
  }, 240)
}

export function setSidebarPinsOpen(open: boolean) {
  $sidebarPinsOpen.set(open)
}

export function setSidebarOverlayMounted(mounted: boolean) {
  $sidebarOverlayMounted.set(mounted)
}

export function setSidebarRecentsOpen(open: boolean) {
  $sidebarRecentsOpen.set(open)
}

export function setSidebarCronOpen(open: boolean) {
  $sidebarCronOpen.set(open)
}

export function toggleSidebarMessagingOpen(sourceId: string) {
  const current = $sidebarMessagingOpenIds.get()

  $sidebarMessagingOpenIds.set(
    current.includes(sourceId) ? current.filter(id => id !== sourceId) : [...current, sourceId]
  )
}

export function setSidebarAgentsGrouped(grouped: boolean) {
  $sidebarAgentsGrouped.set(grouped)
}

export function setSidebarSessionOrderIds(ids: string[]) {
  if (!arraysEqual($sidebarSessionOrderIds.get(), ids)) {
    $sidebarSessionOrderIds.set(ids)
  }
}

export function setSidebarSessionOrderManual(manual: boolean) {
  if ($sidebarSessionOrderManual.get() !== manual) {
    $sidebarSessionOrderManual.set(manual)
  }
}

export function setSidebarWorkspaceOrderIds(ids: string[]) {
  if (!arraysEqual($sidebarWorkspaceOrderIds.get(), ids)) {
    $sidebarWorkspaceOrderIds.set(ids)
  }
}

export function setSidebarWorkspaceParentOrderIds(ids: string[]) {
  if (!arraysEqual($sidebarWorkspaceParentOrderIds.get(), ids)) {
    $sidebarWorkspaceParentOrderIds.set(ids)
  }
}

export function setSidebarProjectOrderIds(ids: string[]) {
  if (!arraysEqual($sidebarProjectOrderIds.get(), ids)) {
    $sidebarProjectOrderIds.set(ids)
  }
}

export function setSidebarResizing(resizing: boolean) {
  $isSidebarResizing.set(resizing)
}

export function pinSession(sessionId: string, index?: number) {
  const prev = $pinnedSessionIds.get()
  const next = insertUniqueId(prev, sessionId, index ?? prev.filter(id => id !== sessionId).length)

  if (!arraysEqual(prev, next)) {
    $pinnedSessionIds.set(next)
  }
}

export function unpinSession(sessionId: string) {
  const prev = $pinnedSessionIds.get()
  const next = prev.filter(id => id !== sessionId)

  if (!arraysEqual(prev, next)) {
    $pinnedSessionIds.set(next)
  }
}

// Replace the whole pinned order at once (drag-reorder hands back the new order
// rather than a single move). Keep only ids that are actually pinned so a stale
// row can't smuggle an unpinned id into the store.
export function setPinnedSessionOrder(ids: string[]) {
  const prev = $pinnedSessionIds.get()
  const pinned = new Set(prev)
  const next = ids.filter(id => pinned.has(id))

  if (next.length === prev.length && !arraysEqual(prev, next)) {
    $pinnedSessionIds.set(next)
  }
}

export function bumpSessionsLimit(step: number = SIDEBAR_SESSIONS_PAGE_SIZE) {
  const safeStep = Math.max(1, Math.floor(step))
  $sessionsLimit.set($sessionsLimit.get() + safeStep)
}

export function resetSessionsLimit() {
  if ($sessionsLimit.get() !== SIDEBAR_SESSIONS_PAGE_SIZE) {
    $sessionsLimit.set(SIDEBAR_SESSIONS_PAGE_SIZE)
  }
}
