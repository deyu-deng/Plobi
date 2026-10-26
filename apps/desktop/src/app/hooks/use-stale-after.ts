import { useEffect, useState } from 'react'

/**
 * 给"不确定还要等多久"的加载态一个上限：`active` 连续超过 `ms` 之后 `stale` 变 true。
 *
 * 事故（2026-10-26 用户报「打开 overlay 会反复闪烁」）：那些弹层里的"加载中"是
 * `animate-pulse` 无限脉冲，而它们等的请求要么 30 秒才超时、要么立刻 404
 * （`Profile 'x' does not exist.`）——加载态永远不收口，屏幕看起来就是一直在闪。
 *
 * 上限之后由调用方把脉冲换成静态 + 一句"还没回来"，别让动画替系统撒谎说"快好了"。
 */
export function useStaleAfter(active: boolean, ms = 8000): boolean {
  const [stale, setStale] = useState(false)

  useEffect(() => {
    if (!active) {
      setStale(false)
      return
    }

    // 已经盯了很久（同一个 active 期间重渲染）就别把表重新起一遍。
    if (stale) {
      return
    }

    const timer = window.setTimeout(() => setStale(true), ms)

    return () => window.clearTimeout(timer)
    // 故意不把 stale 放进依赖：它只由这个定时器置位，`active` 归零时才复位。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, ms])

  return stale
}
