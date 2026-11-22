/**
 * 三态读数的契约（`WP-OFFLOAD-OUTCOME`，11-22）。
 *
 * 屏上要分得开的两件事：**它停下来等一句话**（回它一句就继续）和**它办砸了**
 * （重试或换人）。摆成同一个红字，读的人只会去重试——重试一遍还卡在原地。
 */

import { describe, expect, it } from 'vitest'

import { roundOutcome, waitingKind } from './agent-offloads'
import type { OffloadRoundWithReply } from './agent-offloads'

const round = (patch: Partial<OffloadRoundWithReply>): OffloadRoundWithReply => ({
  agent: 'qoder',
  ...patch,
})

describe('roundOutcome', () => {
  it('keeps a waiting round waiting even when a failure code is attached', () => {
    expect(roundOutcome(round({ outcome: 'waiting', waiting_on: 'permission', failure: 'blocked_by_gate' })))
      .toBe('waiting')
  })

  it('falls back to failure for an old row without the column', () => {
    expect(roundOutcome(round({ failure: 'timeout' }))).toBe('failed')
    expect(roundOutcome(round({}))).toBe('done')
  })

  it('never treats an unknown value as a success', () => {
    expect(roundOutcome(round({ outcome: 'weird', failure: 'timeout' }))).toBe('failed')
    expect(roundOutcome(round({ outcome: 'weird' }))).toBe('done')
  })
})

describe('waitingKind', () => {
  it('is empty unless the round is actually waiting', () => {
    expect(waitingKind(round({ outcome: 'done', waiting_on: 'permission' }))).toBe('')
    expect(waitingKind(round({ outcome: 'failed', waiting_on: 'permission' }))).toBe('')
  })

  it('reports what it waits for, and defaults to an answer', () => {
    expect(waitingKind(round({ outcome: 'waiting', waiting_on: 'permission' }))).toBe('permission')
    expect(waitingKind(round({ outcome: 'waiting', waiting_on: 'turn' }))).toBe('turn')
    expect(waitingKind(round({ outcome: 'waiting' }))).toBe('answer')
  })
})
