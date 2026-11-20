import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  $composerAttachments,
  addComposerAttachment,
  clearSessionDraft,
  type ComposerAttachment,
  removeComposerAttachment,
  SESSION_DRAFTS_STORAGE_KEY,
  stashSessionDraft,
  takeSessionDraft,
  updateComposerAttachment
} from './composer'

function attachment(overrides: Partial<ComposerAttachment> & Pick<ComposerAttachment, 'id'>): ComposerAttachment {
  return { kind: 'file', label: 'doc.pdf', ...overrides }
}

describe('updateComposerAttachment', () => {
  afterEach(() => {
    $composerAttachments.set([])
  })

  it('replaces an existing attachment in place', () => {
    addComposerAttachment(attachment({ id: 'file:a', uploadState: 'uploading' }))

    const updated = updateComposerAttachment(attachment({ id: 'file:a', attachedSessionId: 'sess-1' }))

    expect(updated).toBe(true)
    const current = $composerAttachments.get()
    expect(current).toHaveLength(1)
    expect(current[0]?.attachedSessionId).toBe('sess-1')
    expect(current[0]?.uploadState).toBeUndefined()
  })

  it('does NOT resurrect an attachment the user removed mid-upload', () => {
    // Drop → eager upload starts → user removes the chip → upload resolves.
    // The late success must not re-add the removed attachment.
    addComposerAttachment(attachment({ id: 'file:a', uploadState: 'uploading' }))
    removeComposerAttachment('file:a')

    const updated = updateComposerAttachment(attachment({ id: 'file:a', attachedSessionId: 'sess-1' }))

    expect(updated).toBe(false)
    expect($composerAttachments.get()).toHaveLength(0)
  })
})

describe('session drafts', () => {
  afterEach(() => {
    for (const scope of ['session-a', 'session-b', null]) {
      clearSessionDraft(scope)
    }

    window.localStorage.clear()
  })

  it('keeps drafts isolated per session scope', () => {
    stashSessionDraft('session-a', 'draft a', [])
    stashSessionDraft('session-b', 'draft b', [attachment({ id: 'image:b', kind: 'image' })])

    expect(takeSessionDraft('session-a')).toEqual({ attachments: [], text: 'draft a' })
    expect(takeSessionDraft('session-b').text).toBe('draft b')
    expect(takeSessionDraft('session-b').attachments.map(a => a.id)).toEqual(['image:b'])
  })

  it('scopes the unsaved new-session draft separately from real sessions', () => {
    stashSessionDraft(null, 'new chat draft', [])
    stashSessionDraft('session-a', 'session draft', [])

    expect(takeSessionDraft(null).text).toBe('new chat draft')
    expect(takeSessionDraft(undefined).text).toBe('new chat draft')
    expect(takeSessionDraft('session-a').text).toBe('session draft')
  })

  it('keeps one draft per Agent (the draft bucket is the Agent bucket)', () => {
    // The draft lives in the same bucket as the queue: the Agent's scope key.
    // Switching Agents swaps the draft; coming back to the same Agent finds it
    // again — and the key never changes under it, so a half-written draft can
    // never vanish.
    stashSessionDraft('agent:agenda-secretary', 'agenda draft', [])
    stashSessionDraft('agent:butler', 'butler draft', [])

    expect(takeSessionDraft('agent:agenda-secretary').text).toBe('agenda draft')
    expect(takeSessionDraft('agent:butler').text).toBe('butler draft')
  })

  it('persists the draft (text + the attachment records, but no thumbnails) to localStorage', () => {
    // 11-20 起附件也是**正文里的一枚 chip** ⇒ 附件的记录得跟着草稿一起落盘：
    // 记录丢了，那枚 chip 就成了空壳（发出去时不带它）。
    // **缩略图不落盘**（data URL 可能几兆，localStorage 装不下）⇒ 上传状态也不落
    // （那是一次性的）。
    stashSessionDraft('session-a', 'survives reload', [
      attachment({ id: 'file:a', previewUrl: 'data:image/png;base64,AAA', uploadState: 'uploading' })
    ])

    const persisted = JSON.parse(window.localStorage.getItem(SESSION_DRAFTS_STORAGE_KEY) ?? '{}') as Record<
      string,
      { attachments?: ComposerAttachment[]; text?: string } | string
    >

    expect(persisted['session-a']).toEqual({
      attachments: [{ id: 'file:a', kind: 'file', label: 'doc.pdf' }],
      text: 'survives reload'
    })
  })

  it('reads back a draft persisted in the OLD format (text only, no attachments)', async () => {
    // 旧格式（只存正文）也得读得出来：那会儿附件不是 chip，正文里也没有它们的线上形态。
    // 落盘的草稿是在**模块加载时**读的 ⇒ 这一条得重新加载模块才看得见。
    window.localStorage.setItem(SESSION_DRAFTS_STORAGE_KEY, JSON.stringify({ 'session-old': 'old draft' }))
    vi.resetModules()

    const { takeSessionDraft: take } = await import('./composer')

    expect(take('session-old').text).toBe('old draft')
    expect(take('session-old').attachments).toEqual([])
  })

  it('evicts empty drafts instead of leaving stale entries behind', () => {
    stashSessionDraft('session-a', 'saved', [])
    stashSessionDraft('session-a', '   ', [])

    expect(takeSessionDraft('session-a')).toEqual({ attachments: [], text: '' })
  })

  it('clears a stashed draft after an accepted submit', () => {
    stashSessionDraft('session-a', 'sent prompt', [attachment({ id: 'file:a' })])
    clearSessionDraft('session-a')

    expect(takeSessionDraft('session-a')).toEqual({ attachments: [], text: '' })
  })

  it('returns clones so callers cannot mutate the stash', () => {
    stashSessionDraft('session-a', 'draft', [attachment({ id: 'file:a' })])

    const taken = takeSessionDraft('session-a')
    taken.attachments[0]!.label = 'mutated'

    expect(takeSessionDraft('session-a').attachments[0]?.label).toBe('doc.pdf')
  })
})
