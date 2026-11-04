import { describe, expect, it } from 'vitest'

import { externalSenderOf, quotedPreview, splitFilePointers, splitSpeakerPrefix } from './chat-messages'

describe('externalSenderOf', () => {
  it('reads the new kind column first', () => {
    expect(externalSenderOf('agent', 'say:agenda')).toEqual({ kind: 'agent', name: 'agenda' })
    expect(externalSenderOf('cron', 'cron:job1')).toEqual({ kind: 'cron', name: '' })
  })

  it('falls back to asked_by for rows written before the column existed', () => {
    expect(externalSenderOf(null, 'say:agenda')).toEqual({ kind: 'agent', name: 'agenda' })
    expect(externalSenderOf(undefined, 'cron:01bee4f6f8cd')).toEqual({ kind: 'cron', name: '' })
  })

  it('falls back to the body prefix when the history arm dropped both columns', () => {
    // 网关 `_history_to_messages` 只带 role+text；前缀是那条路上最后一份身份。
    expect(externalSenderOf(undefined, undefined, '[来自 plobi · 2026-10-24T11:08:40]\n自检')).toEqual({
      kind: 'agent',
      name: ''
    })
    expect(externalSenderOf(undefined, undefined, '[Cron 结果 · 早报 · 2026-10-24 08:00:00]\n早')).toEqual({
      kind: 'cron',
      name: ''
    })
    expect(externalSenderOf(undefined, undefined, '[定时任务 · 早报 · 2026-10-24 08:00:00]\n读安排')).toEqual({
      kind: 'cron',
      name: ''
    })
    expect(externalSenderOf(undefined, undefined, '你好')).toBeNull()
  })

  it('keeps system events out of the conversation shapes', () => {
    expect(externalSenderOf('system', 'say:plobi')).toEqual({ kind: 'system', name: 'plobi' })
    expect(externalSenderOf('system', undefined, '【plobi 刚给你发了一条消息】…')).toEqual({
      kind: 'system',
      name: ''
    })
  })

  it('names the secretary for l1-keyed agent rows', () => {
    expect(externalSenderOf('agent', 'l1:sess-1')).toEqual({ kind: 'agent', name: '总秘书' })
  })

  it('leaves owner-keyed rows on the human bubble', () => {
    expect(externalSenderOf('user', 'owner')).toBeNull()
    expect(externalSenderOf(null, null)).toBeNull()
    expect(externalSenderOf(undefined, undefined)).toBeNull()
  })
})

describe('splitSpeakerPrefix', () => {
  it('splits the say prefix into speaker and body', () => {
    expect(splitSpeakerPrefix('[来自 plobi · 2026-10-24T11:08:40]\n你好')).toEqual({
      speaker: 'plobi',
      body: '你好'
    })
  })

  it('splits the cron prefix and keeps the job name', () => {
    expect(splitSpeakerPrefix('[Cron 结果 · plobi-morning-report · 2026-10-23 08:00:00]\n早报')).toEqual({
      speaker: 'plobi-morning-report',
      body: '早报'
    })
    expect(splitSpeakerPrefix('[定时任务 · plobi-morning-report · 2026-10-24 08:00:00]\n读安排')).toEqual({
      speaker: 'plobi-morning-report',
      body: '读安排'
    })
  })

  it('leaves a body that only looks bracketed alone', () => {
    expect(splitSpeakerPrefix('[备注] 这不是前缀')).toEqual({ speaker: '', body: '[备注] 这不是前缀' })
    expect(splitSpeakerPrefix('没有前缀')).toEqual({ speaker: '', body: '没有前缀' })
  })
})

describe('splitFilePointers', () => {
  it('pulls file pointers out of the paragraph', () => {
    expect(splitFilePointers('先看这段\n[文件] surface/proj/note.md\n看完再说')).toEqual([
      { kind: 'text', text: '先看这段' },
      { kind: 'file', path: 'surface/proj/note.md' },
      { kind: 'text', text: '看完再说' }
    ])
  })

  it('leaves a normal paragraph alone', () => {
    expect(splitFilePointers('没有指针')).toEqual([{ kind: 'text', text: '没有指针' }])
    expect(splitFilePointers('')).toEqual([])
  })
})

describe('quotedPreview', () => {
  it('strips the speaker prefix and collapses the quote into one line', () => {
    expect(quotedPreview({ id: 7, content: '[来自 plobi · 2026-10-24T11:08:40]\n第一行\n第二行' })).toEqual({
      id: '7',
      text: '第一行 第二行'
    })
  })

  it('returns null when the row is missing or empty', () => {
    expect(quotedPreview(undefined)).toBeNull()
    expect(quotedPreview({ id: 7, content: '' })).toBeNull()
  })

  it('truncates a long quote', () => {
    const preview = quotedPreview({ id: 1, content: 'x'.repeat(200) })
    expect(preview?.text.length).toBe(80)
  })
})
