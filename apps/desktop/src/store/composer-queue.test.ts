import { beforeEach, describe, expect, it } from 'vitest'

import type { ComposerAttachment } from './composer'
import {
  $queuedPromptsByAgent,
  clearQueuedPrompts,
  dequeueQueuedPrompt,
  enqueueQueuedPrompt,
  getQueuedPrompts,
  promoteQueuedPrompt,
  removeQueuedPrompt,
  shouldAutoDrain,
  updateQueuedPrompt,
  updateQueuedPromptText
} from './composer-queue'

// 桶＝**Agent 的作用域键**（`l1` / `agent:<id>`），不是会话 id。
const L1_BUCKET = 'l1'
const AGENT_A_BUCKET = 'agent:agenda-secretary'
const AGENT_B_BUCKET = 'agent:butler'
const QUEUE_STORAGE_KEY = 'plobi.desktop.composerQueue.v1'

function attachment(id: string, kind: ComposerAttachment['kind'] = 'file'): ComposerAttachment {
  return {
    id,
    kind,
    label: id,
    refText: `@file:${id}`
  }
}

describe('composer queue store', () => {
  beforeEach(() => {
    window.localStorage.removeItem(QUEUE_STORAGE_KEY)
    $queuedPromptsByAgent.set({})
  })

  it('queues prompts in FIFO order', () => {
    enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'first' })
    enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'second' })

    expect(dequeueQueuedPrompt(AGENT_A_BUCKET)?.text).toBe('first')
    expect(dequeueQueuedPrompt(AGENT_A_BUCKET)?.text).toBe('second')
    expect(dequeueQueuedPrompt(AGENT_A_BUCKET)).toBeNull()
  })

  it('clones attachments when queueing', () => {
    const source = [attachment('a-1')]
    const queued = enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: source, text: 'check clones' })

    expect(queued).not.toBeNull()
    expect(getQueuedPrompts(AGENT_A_BUCKET)[0]?.attachments[0]).toEqual(source[0])
    expect(getQueuedPrompts(AGENT_A_BUCKET)[0]?.attachments[0]).not.toBe(source[0])
  })

  it('updates and removes queued entries by id', () => {
    const first = enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'draft one' })
    const second = enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'draft two' })

    expect(first).not.toBeNull()
    expect(second).not.toBeNull()

    expect(updateQueuedPromptText(AGENT_A_BUCKET, first!.id, 'draft one edited')).toBe(true)
    expect(getQueuedPrompts(AGENT_A_BUCKET).map(entry => entry.text)).toEqual(['draft one edited', 'draft two'])

    expect(removeQueuedPrompt(AGENT_A_BUCKET, first!.id)).toBe(true)
    expect(getQueuedPrompts(AGENT_A_BUCKET).map(entry => entry.text)).toEqual(['draft two'])
  })

  it('promotes a queued entry to the front', () => {
    const first = enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'first' })
    const second = enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'second' })
    const third = enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'third' })

    expect(first).not.toBeNull()
    expect(second).not.toBeNull()
    expect(third).not.toBeNull()

    expect(promoteQueuedPrompt(AGENT_A_BUCKET, third!.id)).toBe(true)
    expect(getQueuedPrompts(AGENT_A_BUCKET).map(entry => entry.text)).toEqual(['third', 'first', 'second'])
    expect(promoteQueuedPrompt(AGENT_A_BUCKET, third!.id)).toBe(false)
  })

  it('updates queued text and attachment snapshot', () => {
    const first = enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [attachment('f-1')], text: 'draft one' })
    const editedAttachments = [attachment('f-2'), attachment('f-3', 'image')]

    expect(first).not.toBeNull()
    expect(
      updateQueuedPrompt(AGENT_A_BUCKET, first!.id, {
        attachments: editedAttachments,
        text: 'edited text'
      })
    ).toBe(true)

    const queue = getQueuedPrompts(AGENT_A_BUCKET)
    expect(queue[0]?.text).toBe('edited text')
    expect(queue[0]?.attachments).toEqual(editedAttachments)
    expect(queue[0]?.attachments[0]).not.toBe(editedAttachments[0])
  })

  it('clears queue state for a bucket', () => {
    enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [attachment('img-1', 'image')], text: 'queued' })

    clearQueuedPrompts(AGENT_A_BUCKET)

    expect(getQueuedPrompts(AGENT_A_BUCKET)).toEqual([])
    expect($queuedPromptsByAgent.get()[AGENT_A_BUCKET]).toBeUndefined()
    expect(window.localStorage.getItem(QUEUE_STORAGE_KEY)).toBeNull()
  })

  it('persists queue entries into local storage', () => {
    enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'persist me' })

    const raw = window.localStorage.getItem(QUEUE_STORAGE_KEY)
    expect(raw).toBeTruthy()

    const parsed = JSON.parse(String(raw)) as Record<string, { text: string }[]>
    expect(parsed[AGENT_A_BUCKET]?.[0]?.text).toBe('persist me')
  })
})

// The rule this file pins: **the bucket is the Agent**, so it always exists and
// never changes. One Agent, one mouth (裁定 37／R-015) — there is no session
// concept left to key the queue by.
describe('the queue bucket is the Agent', () => {
  beforeEach(() => {
    window.localStorage.removeItem(QUEUE_STORAGE_KEY)
    $queuedPromptsByAgent.set({})
  })

  it('gives every Agent its own bucket (two Agents never share one)', () => {
    expect(L1_BUCKET).not.toBe(AGENT_A_BUCKET)
    expect(AGENT_A_BUCKET).not.toBe(AGENT_B_BUCKET)

    enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'for agenda' })
    enqueueQueuedPrompt(AGENT_B_BUCKET, { attachments: [], text: 'for butler' })

    expect(getQueuedPrompts(AGENT_A_BUCKET).map(e => e.text)).toEqual(['for agenda'])
    expect(getQueuedPrompts(AGENT_B_BUCKET).map(e => e.text)).toEqual(['for butler'])
  })

  it('keeps a message in the bucket it was typed in when the session changes', () => {
    // The bucket does NOT move when the session id changes (new session,
    // resume, backend bounce): the Agent is the same Agent. So no migration
    // is needed — and no message can end up in two buckets.
    enqueueQueuedPrompt(AGENT_A_BUCKET, { attachments: [], text: 'typed before the session existed' })

    expect(getQueuedPrompts(AGENT_A_BUCKET).map(e => e.text)).toEqual(['typed before the session existed'])
    expect($queuedPromptsByAgent.get()[AGENT_A_BUCKET]).toHaveLength(1)
  })

  it('refuses an empty bucket key (a queue entry must always have a home)', () => {
    expect(enqueueQueuedPrompt(null, { attachments: [], text: 'nowhere' })).toBeNull()
    expect(enqueueQueuedPrompt('   ', { attachments: [], text: 'nowhere' })).toBeNull()
    expect(getQueuedPrompts(null)).toEqual([])
  })
})

describe('shouldAutoDrain', () => {
  it('drains whenever idle with a non-empty queue', () => {
    expect(shouldAutoDrain({ isBusy: false, queueLength: 1 })).toBe(true)
  })

  it('drains on mount/reconnect with no observed busy edge', () => {
    // The whole point of dropping the edge: a remount resets the busy ref, so an
    // edge-gated drain would strand the entry. Idle + non-empty must still fire.
    expect(shouldAutoDrain({ isBusy: false, queueLength: 2 })).toBe(true)
  })

  it('does not drain mid-turn', () => {
    expect(shouldAutoDrain({ isBusy: true, queueLength: 1 })).toBe(false)
  })

  it('does not drain an empty queue', () => {
    expect(shouldAutoDrain({ isBusy: false, queueLength: 0 })).toBe(false)
  })
})
