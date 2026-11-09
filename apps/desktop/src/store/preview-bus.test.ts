import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./preview', () => ({
  setCurrentSessionPreviewTarget: vi.fn()
}))

import { setCurrentSessionPreviewTarget } from './preview'
import {
  $previewBusItems,
  applyHighestPriorityPreview,
  pushPreviewBusItem
} from './preview-bus'

describe('preview-bus', () => {
  beforeEach(() => {
    $previewBusItems.set([])
    vi.clearAllMocks()
  })

  it('sorts artifact before progress and resource', () => {
    pushPreviewBusItem({ title: 'r', priority: 'resource', autoOpen: false })
    pushPreviewBusItem({ title: 'a', priority: 'artifact', autoOpen: false })
    pushPreviewBusItem({ title: 'p', priority: 'progress', autoOpen: false })
    expect($previewBusItems.get().map(i => i.title)).toEqual(['a', 'p', 'r'])
  })

  it('auto-opens an artifact into the workbench card', () => {
    pushPreviewBusItem({
      title: 'out.html',
      priority: 'artifact',
      kind: 'file',
      path: 'D:/tmp/out.html',
      autoOpen: true
    })
    // 预览轨删了 ⇒ 自动推送的落点是工作区卡片的一个页签（`setCurrentSessionPreviewTarget`
    // 内部走 `requestWorkbenchFileOpen`）。
    expect(setCurrentSessionPreviewTarget).toHaveBeenCalled()
  })

  it('applyHighestPriorityPreview prefers artifact', () => {
    $previewBusItems.set([
      { title: 'r', priority: 'resource' },
      { title: 'a', priority: 'artifact', path: 'D:/a.md', kind: 'file' }
    ])
    const hit = applyHighestPriorityPreview()
    expect(hit?.title).toBe('a')
  })
})
