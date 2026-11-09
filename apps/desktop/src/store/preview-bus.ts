/**
 * North Star preview bus (desktop).
 *
 * Priority: artifact (产出) > progress (进度) > resource (资源).
 * Auto-push opens the file as one tab of the **workbench card**.
 *
 * 以前它推的是**对话旁那条预览轨**（`setPaneOpen(PREVIEW_PANE_ID)` ＋
 * `selectRightRailTab(RIGHT_RAIL_PREVIEW_TAB_ID)`）。那条轨删掉了
 * （用户 11-09 11:47：「以后只有两张卡片了吗？一个叫工作区卡片。一个叫对话区卡片。」），
 * 于是自动推送的落点跟着改成工作区卡片的一个页签。
 */

import { atom } from 'nanostores'

import {
  type PreviewRecordSource,
  type PreviewTarget,
  setCurrentSessionPreviewTarget
} from './preview'

export type PreviewBusPriority = 'artifact' | 'progress' | 'resource'

export interface PreviewBusItem {
  autoOpen?: boolean
  id?: string
  kind?: 'file' | 'url' | 'text'
  meta?: Record<string, unknown>
  path?: string
  priority?: PreviewBusPriority | number
  text?: string
  title: string
  url?: string
}

const PRIORITY_RANK: Record<PreviewBusPriority, number> = {
  artifact: 0,
  progress: 1,
  resource: 2
}

/** Last bus items received this session (newest last). */
export const $previewBusItems = atom<PreviewBusItem[]>([])

function rank(priority: PreviewBusItem['priority']): number {
  if (typeof priority === 'number') {
    return priority
  }

  return PRIORITY_RANK[priority ?? 'artifact'] ?? 0
}

function toPreviewTarget(item: PreviewBusItem): PreviewTarget | null {
  if (item.url || item.kind === 'url') {
    return {
      kind: 'url',
      label: item.title,
      source: item.url || item.title,
      url: item.url || 'about:blank'
    }
  }

  if (item.path || item.kind === 'file') {
    const path = item.path || ''
    const url = path ? `file://${path}` : item.url || ''
    if (!url && !path) {
      return null
    }

    return {
      kind: 'file',
      label: item.title,
      path,
      source: path || url,
      url: url || `file://${path}`
    }
  }

  // text-only: surface as url data placeholder label; the tab still opens
  return {
    kind: 'url',
    label: item.title,
    source: item.text || item.title,
    url: 'about:blank'
  }
}

/**
 * Push a bus item. Higher-priority (lower rank) auto-open items win when
 * several arrive close together — callers should push artifacts first.
 */
export function pushPreviewBusItem(item: PreviewBusItem, source: PreviewRecordSource = 'tool-result'): PreviewBusItem {
  const next = [...$previewBusItems.get(), item].slice(-100)
  next.sort((a, b) => rank(a.priority) - rank(b.priority))
  $previewBusItems.set(next)

  const auto = item.autoOpen !== false
  if (auto) {
    const target = toPreviewTarget(item)
    if (target) {
      // 这一条现在落进**工作区卡片**（`setCurrentSessionPreviewTarget` 内部
      // 走 `requestWorkbenchFileOpen`），对话旁不再有第三块。
      setCurrentSessionPreviewTarget(target, source)
    }
  }

  return item
}

/** Apply the highest-priority pending auto item (e.g. after polling host bus). */
export function applyHighestPriorityPreview(): PreviewBusItem | null {
  const items = [...$previewBusItems.get()].sort((a, b) => rank(a.priority) - rank(b.priority))
  const hit = items.find(i => i.autoOpen !== false)
  if (!hit) {
    return null
  }
  pushPreviewBusItem(hit)
  return hit
}
