const IDLE_MS = 800

/** 每个正在滑的元素各自一个淡出定时器；WeakMap ⇒ 元素被回收就一起没了。 */
const timers = new WeakMap<Element, number>()

/**
 * 导条只在"真的在滑"的那块上出现，停手 800ms 淡出（用户 11-03）。
 *
 * 全局挂一个捕获监听，而不是每个滚动区各自接：一处接线，侧栏、正文、工作台、
 * 浮层里那些 `overflow-y-auto` 全都吃到，新加的滚动区也不会漏。可见性由 CSS 里
 * `[data-scrolling='true']` 那几条说话，这里只负责挂/摘这个属性。
 */
export function startScrollActivityWatch(): () => void {
  const handleScroll = (event: Event) => {
    const target = event.target

    if (!(target instanceof Element)) {
      return
    }

    target.setAttribute('data-scrolling', 'true')

    const pending = timers.get(target)

    if (pending) {
      window.clearTimeout(pending)
    }

    timers.set(
      target,
      window.setTimeout(() => {
        timers.delete(target)
        target.removeAttribute('data-scrolling')
      }, IDLE_MS)
    )
  }

  document.addEventListener('scroll', handleScroll, { capture: true, passive: true })

  return () => {
    document.removeEventListener('scroll', handleScroll, { capture: true })
  }
}
