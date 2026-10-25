import { describe, expect, it } from 'vitest'

import { externalSenderOf, splitSpeakerPrefix } from './chat-messages'

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
