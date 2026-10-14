import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import {
  artifactPathOf,
  externalHandleOf,
  fileUrlFor,
  groupRoundsByTarget,
  handleJumpEligible,
  jumpTargetFor,
  type OffloadRound,
  roundFacts,
} from './agent-offloads'

function round(over: Partial<OffloadRound> = {}): OffloadRound {
  return {
    agent: 'codebuddy',
    artifacts: [{ path: 'D:/projects/Aura/today.py', sha256_12: '3da942743391' }],
    brief: '把今天的读数写成页',
    cost_status: 'usage_reported_without_price',
    cwd: 'D:/projects/Aura',
    duration_seconds: 16.4,
    permission_flag: '-y --permission-mode=bypassPermissions',
    ts: '2026-10-13T01:00:00+00:00',
    ...over,
  }
}

describe('groupRoundsByTarget', () => {
  it('按外包对象分组，组内新的在前，组间按名字稳定', () => {
    const groups = groupRoundsByTarget([
      round({ ts: '2026-10-11T00:00:00+00:00' }),
      round({ agent: 'agy', ts: '2026-10-12T00:00:00+00:00' }),
      round({ ts: '2026-10-13T00:00:00+00:00' }),
    ])

    expect(groups.map(g => g.target)).toEqual(['agy', 'codebuddy'])
    expect(groups[1]!.rounds.map(r => r.ts)).toEqual([
      '2026-10-13T00:00:00+00:00',
      '2026-10-11T00:00:00+00:00',
    ])
  })

  it('没有对象名的行归到 unknown，不被丢掉', () => {
    expect(groupRoundsByTarget([round({ agent: '  ' })]).map(g => g.target)).toEqual(['unknown'])
  })

  it('空表就是空表', () => {
    expect(groupRoundsByTarget([])).toEqual([])
  })
})

describe('fileUrlFor —— 裸路径主进程不认，必须给 file://', () => {
  it('反斜杠与正斜杠都能落回同一个路径', () => {
    for (const raw of ['D:\\projects\\Aura\\today.py', 'D:/projects/Aura/today.py']) {
      expect(fileURLToPath(new URL(fileUrlFor(raw)))).toBe('D:\\projects\\Aura\\today.py')
    }
  })

  it('空格、中文、# 都要编码，否则 URL 解析会把后半截当片段', () => {
    const url = fileUrlFor('D:/数据 库/笔记 #1.md')

    expect(url).not.toContain(' ')
    expect(url).not.toContain('#')
    expect(fileURLToPath(new URL(url))).toBe('D:\\数据 库\\笔记 #1.md')
  })

  it('开头的斜杠不许多出一层空段', () => {
    expect(fileUrlFor('/tmp/a b.txt')).toBe('file:///tmp/a%20b.txt')
  })
})

describe('jumpTargetFor —— 跳不进会话就不许给一个假的可点行', () => {
  it('通道不回会话号 ⇒ 有产物也只能退成"打开落盘的东西"', () => {
    const plan = jumpTargetFor(round())

    expect(plan.kind).toBe('reveal')
    expect(plan.reason).toBe('')
    expect(plan.url).toBe('file:///D:/projects/Aura/today.py')
  })

  it('没有产物（或产物路径是空的）：不可点，原因分两种', () => {
    for (const over of [{ artifacts: [] }, { artifacts: [{ path: '  ' }] }, { artifacts: undefined }]) {
      const plan = jumpTargetFor(round(over))

      expect(plan.kind).toBe('none')
      expect(plan.reason).toBe('no-artifact')
      expect(plan.url).toBe('')
    }

    // 台账带回了会话号，缺的就只是"跳进去的那条通道"，别把两种混成一句话
    const withHandle = jumpTargetFor(round({ artifacts: [], external_session: '01a1248d-3b15' }))

    expect(withHandle.kind).toBe('none')
    expect(withHandle.reason).toBe('no-jump-transport')
    expect(withHandle.url).toBe('')
  })

  it('契约第 4 条：空串永远不许让那一支变可点', () => {
    for (const raw of ['', '   ', undefined]) {
      expect(handleJumpEligible(round({ external_session: raw }))).toBe(false)
      expect(jumpTargetFor(round({ artifacts: [], external_session: raw })).reason).toBe('no-artifact')
    }

    expect(handleJumpEligible(round({ external_session: '01a1248d-3b15' }))).toBe(true)
  })

  it('会话号原样读出，来源格单独可见（厂商改信封时要知道读了哪个键）', () => {
    expect(externalHandleOf(round({ external_session: ' 01a1248d-3b15 ' }))).toBe('01a1248d-3b15')
    expect(round({ external_session_source: 'result.session_id' }).external_session_source).toBe('result.session_id')
  })

  it('artifactPathOf 取第一个有值的产物，不取空串', () => {
    expect(artifactPathOf(round({ artifacts: [{ path: '' }, { path: 'D:/x/y.md' }] }))).toBe('D:/x/y.md')
  })
})

describe('roundFacts —— 一行上的读数（判据 2：谁·目录·权限档·花费）', () => {
  it('金额算不出是 null 并带上原因，绝不报 0（R-073）', () => {
    const facts = roundFacts(round())

    expect(facts.costUsd).toBeNull()
    expect(facts.costStatus).toBe('usage_reported_without_price')
  })

  it('真报价为 0 就是 0，不被当成"没报"', () => {
    expect(roundFacts(round({ usage: { cost_usd: 0 } })).costUsd).toBe(0)
  })

  it('耗时缺省或 0 ⇒ null（渲染层就不会写出 "0s"）', () => {
    expect(roundFacts(round({ duration_seconds: 0 })).durationSeconds).toBeNull()
    expect(roundFacts(round({ duration_seconds: undefined })).durationSeconds).toBeNull()
    expect(roundFacts(round({ duration_seconds: 16.4 })).durationSeconds).toBe(16.4)
  })

  it('权限档原样带出来', () => {
    expect(roundFacts(round()).permissionFlag).toBe('-y --permission-mode=bypassPermissions')
  })
})
