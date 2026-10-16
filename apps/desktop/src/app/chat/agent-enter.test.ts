import { describe, expect, it } from 'vitest'

import {
  beginEntering,
  endEntering,
  enterFailure,
  showsTransition,
  swapLabel,
} from './agent-enter'

/**
 * `WP-RAIL-SWITCH-LOADING` 的两条钉子。断的是关系，不是文案：
 * 失败必须**不再顶着"加载中"**（那正是今天立下的口径 27 的反面），
 * 三种失败是三个键，过渡必须覆盖"点下去那一帧"到"转录到"整段。
 */

describe('进 L2 的过渡节点', () => {
  it('点下去那一帧就有态，任何一条出口都不许把它留在场上', () => {
    const entering = beginEntering('日程秘书')

    expect(showsTransition(entering)).toBe(true)
    expect(entering.label).toBe('日程秘书')

    // 三条出口：成功、被更新的一次进入顶掉、以及失败——都不许留在 entering。
    for (const exit of [endEntering(), enterFailure('bind'), enterFailure('spawn', 'boom')]) {
      expect(showsTransition(exit)).toBe(false)
    }

    // 失败给的是**那一句**（键在界面上本地化），不是一根转不完的圈。
    const failed = enterFailure('no-overview')

    expect(failed.status).toBe('failed')
    expect(failed.reasonKey).toBe('agentEnterNoOverview')
    expect(showsTransition(failed)).toBe(false)
  })

  it('三种失败是三个键，未知种类不猜；遮罩上永远不出现 profile 目录名', () => {
    const keys = ['no-overview', 'spawn', 'bind'].map(code => enterFailure(code as 'bind').reasonKey)

    expect(new Set(keys).size).toBe(3)
    // 不认识的种类：不挑一个最像的顶上去，也不给一个能本地化的键。
    const unknown = enterFailure('whatever' as 'bind')

    expect(unknown.reasonKey).toBeNull()
    expect(unknown.detail).toContain('没记到')

    const agents = [{ id: 'agenda', name: '日程秘书', profile: 'l2-agenda' }]

    expect(swapLabel(agents, 'L2-AGENDA')).toBe('日程秘书')      // 大小写不敏感
    expect(swapLabel(agents, 'l2-course-eet-b')).toBeNull()      // 查不到 ⇒ null，交给中性那句
    expect(swapLabel(agents, null)).toBeNull()
  })
})
