import { useEffect, useState } from 'react'

/**
 * 量一个**容器**的 inline-size —— JS 版的容器查询。
 *
 * 用户 11-16 19:4x 拍板：「这一列先只做**容器查询**（宽度不够就折叠、不遮对话）」。
 * Qoder 那边是纯 CSS（`@container new-chat-b1 (max-width:639.98px)`），我们这边
 * 用 JS 量，原因有两条，都写在 `store/layout.ts` 的 `$chatCardWidth` 注释里：
 * ① 档案卡是对话卡的**兄弟**，CSS 容器查询管不到兄弟；
 * ② 我们要按宽度改**点击行为**（窄时点图标弹贴头浮层、宽时点图标切换显隐），
 * 纯 CSS 做不到。
 *
 * **传元素，不传 ref**：元素换了（对话卡在主区与右栏之间搬）时，`ref` 对象是稳的，
 * 观察会挂在旧节点上；元素状态一变，effect 就重跑，观察跟着搬家。
 */
export function useContainerWidth(element: HTMLElement | null): number {
  const [width, setWidth] = useState(0)

  useEffect(() => {
    if (!element) {
      setWidth(0)

      return undefined
    }

    const measure = () => setWidth(element.getBoundingClientRect().width)

    measure()

    // jsdom 与旧内核没有 ResizeObserver：量一次就够（真实浏览器有它）。
    if (typeof ResizeObserver === 'undefined') {
      return undefined
    }

    const observer = new ResizeObserver(() => measure())

    observer.observe(element)

    return () => observer.disconnect()
  }, [element])

  return width
}
