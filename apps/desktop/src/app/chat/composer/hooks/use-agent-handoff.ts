import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $agentEnter } from '@/app/chat/agent-enter-store'
import { $agentHandoff, takeAgentHandoff } from '@/store/agent-handoff'
import { $scopeAgent, normalizeProfileKey } from '@/store/scope'

/**
 * 交付「发给别的智能体」那笔待办（WP-SELECTION-TOOLBAR）。
 *
 * 待办记下的时候，屏幕还没跳到目标那一屏、后端也还没换成它那一份。所以这一档
 * **只在该发的那一刻发**：三个条件全对上才取走待办并按平常那条路送出去——
 *
 *   1. 作用域键对上了（这一屏就是目标那个智能体的嘴）；
 *   2. 不是「正在进入」中（控制台那一趟进入还没走完：换后端 → 拉会话 → 恢复那一轮）；
 *   3. 活动后端就是目标那个（进入失败时控制台会把后端退回去——那时候不许发，
 *      否则这句话会落进**上一个**智能体的嘴里）。
 *
 * 条件对不上就**不取**：待办留在那儿，等目标那一屏真的就位了再发；一句话都不丢。
 */
export function useAgentHandoff(options: { scopeKey: string; submit: (text: string) => void }) {
  const { scopeKey, submit } = options
  const handoff = useStore($agentHandoff)
  const enterPhase = useStore($agentEnter)
  const scopeAgent = useStore($scopeAgent)

  // `submit` 每次渲染都是新函数（提交通道不是 memo 的），放进依赖里等于每帧重跑
  // 一次这个 effect——用 ref 把它按住，只在条件真的变了才看它。
  const submitRef = useRef(submit)

  submitRef.current = submit

  useEffect(() => {
    if (!handoff || handoff.targetKey !== scopeKey) {
      return
    }

    if (enterPhase.status === 'entering') {
      return
    }

    if (normalizeProfileKey(scopeAgent) !== handoff.agentId) {
      return
    }

    const taken = takeAgentHandoff(handoff.targetKey)

    if (!taken) {
      return
    }

    submitRef.current(taken.text)
  }, [enterPhase.status, handoff, scopeAgent, scopeKey])
}
