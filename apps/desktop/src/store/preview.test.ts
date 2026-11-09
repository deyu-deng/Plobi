import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  $activePreviewTarget,
  $previewServerRestart,
  $previewServerRestartStatus,
  $sessionPreviewRegistry,
  beginPreviewServerRestart,
  clearSessionPreviewRegistry,
  getSessionPreviewRecord,
  previewSelectionLabel,
  type PreviewTarget,
  progressPreviewServerRestart,
  setCurrentSessionPreviewTarget
} from './preview'
import { $activeSessionId, $selectedStoredSessionId } from './session'
import { $workbenchFileOpenRequest } from './workbench-tabs'

function previewTarget(source: string): PreviewTarget {
  return {
    kind: 'file',
    label: source.split(/[/\\]/).filter(Boolean).pop() ?? source,
    path: source,
    previewKind: 'html',
    source,
    url: `file://${source}`
  }
}

function withRenderMode(target: PreviewTarget, renderMode: PreviewTarget['renderMode']): PreviewTarget {
  return { ...target, renderMode }
}

describe('preview store', () => {
  beforeEach(() => {
    $previewServerRestart.set(null)
    $activeSessionId.set('session-1')
    $selectedStoredSessionId.set(null)
    $workbenchFileOpenRequest.set(null)
    window.localStorage.clear()
    clearSessionPreviewRegistry()
  })

  afterEach(() => {
    $previewServerRestart.set(null)
    $activeSessionId.set(null)
    $selectedStoredSessionId.set(null)
    $workbenchFileOpenRequest.set(null)
    window.localStorage.clear()
    clearSessionPreviewRegistry()
  })

  it('does not notify status subscribers for restart progress text', () => {
    const statuses: string[] = []
    const unsubscribe = $previewServerRestartStatus.subscribe(status => statuses.push(status))

    beginPreviewServerRestart('task-1', 'http://localhost:5174')
    progressPreviewServerRestart('task-1', 'first line')
    progressPreviewServerRestart('task-1', 'second line')
    unsubscribe()

    expect(statuses).toEqual(['idle', 'running'])
  })

  it('opens a preview as a tab of the workbench card, not on a preview rail', () => {
    const target = previewTarget('/work/demo.html')

    setCurrentSessionPreviewTarget(target, 'tool-result')

    // 通道里躺着一条「在工作区卡片里开这个」的请求，页签由 desktop-controller 落。
    expect($workbenchFileOpenRequest.get()).toEqual({
      target: withRenderMode(target, 'preview'),
      title: target.label
    })
    expect($activePreviewTarget.get()).toEqual(withRenderMode(target, 'preview'))
  })

  it('persists registered previews per session', () => {
    const target = previewTarget('/work/demo.html')

    setCurrentSessionPreviewTarget(target, 'tool-result')

    expect(getSessionPreviewRecord('session-1')?.normalized).toEqual(withRenderMode(target, 'preview'))
    expect(window.localStorage.getItem('plobi.desktop.sessionPreviews.v1')).toContain('/work/demo.html')
  })

  it('replaces the session preview instead of keeping a back stack', () => {
    const first = previewTarget('/work/first.html')
    const second = previewTarget('/work/second.html')

    setCurrentSessionPreviewTarget(first, 'tool-result')
    setCurrentSessionPreviewTarget(second, 'tool-result')

    expect($sessionPreviewRegistry.get()['session-1']).toHaveLength(1)
    expect(getSessionPreviewRecord('session-1')?.normalized).toEqual(withRenderMode(second, 'preview'))
    expect($sessionPreviewRegistry.get()['session-1']?.map(record => record.normalized.url)).toEqual([
      'file:///work/second.html'
    ])
  })

  it('peeking at a file is not remembered as the session preview', () => {
    const target = previewTarget('/work/demo.html')

    setCurrentSessionPreviewTarget(target, 'file-browser')

    // 文件浏览是「看一眼这个文件」⇒ 不进会话记忆，但**照样开页签**。
    expect(getSessionPreviewRecord('session-1')).toBeNull()
    expect($workbenchFileOpenRequest.get()?.target).toEqual(withRenderMode(target, 'source'))
  })

  it('names the file that is open for a selection carried into the composer', () => {
    setCurrentSessionPreviewTarget(previewTarget('/work/notes/rust.md'), 'tool-result')

    expect(previewSelectionLabel()).toBe('rust.md')
  })
})
