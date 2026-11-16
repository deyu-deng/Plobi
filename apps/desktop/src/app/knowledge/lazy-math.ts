import katex from 'katex'

/**
 * 视口里才干活的那一族：公式（KaTeX）与本地图（读盘转 data URL）。
 *
 * **为什么要有这一档**：长笔记整篇铺进渲染层是**冻**的根因——
 * 《大学物理》1775 块、2342 个公式：KaTeX 整篇排一遍 346 ms，排出来的 DOM 约
 * 7 万个节点；图片则是几十次 IPC 读盘＋base64 一起涌上来。
 *
 * Obsidian 不冻，是因为它**只渲染视口附近**：编辑器（CodeMirror）才是布局源，
 * 渲染只是视口那几行的装饰。我们这边还没有那一套（那是另一刀），所以先做
 * 这一层：**进了视口再排**。
 *
 * 两条规矩：
 *   ① **占位要留得住位置**：公式没排之前占一行，行高与排好后接近 ⇒ 滚到眼前
 *      时那一跳不刺眼。
 *   ② **没��� IntersectionObserver 就立刻排**（老浏览器／测试环境）——宁可慢，
 *      也不能让公式永远不出现。
 */

/** 进视口前多少像素开始排（提前量：滚到眼前时它已经排好了）。 */
const ROOT_MARGIN = '600px 0px'

/** 观察器装不上（老环境／测试）时的兜底：立刻排。 */
function renderNow(element: HTMLElement): void {
  const value = element.getAttribute('data-katex-lazy') ?? ''
  const display = element.getAttribute('data-katex-display') === 'true'

  element.innerHTML = katex.renderToString(value, { displayMode: display, throwOnError: false })
  element.classList.add('katex')
  // 占位那会儿是**透明**的（源码不给人看，也不占视觉重量）；排好后把透明撤掉。
  element.style.color = ''
  element.removeAttribute('data-katex-lazy')
  element.removeAttribute('data-katex-display')
}

/**
 * 把容器里的懒占位公式接上观察器：**进视口才排**。
 *
 * 返回清理函数（组件卸载时调用）。
 */
export function mountLazyMath(container: HTMLElement | null): () => void {
  if (!container) {
    return () => {}
  }

  if (typeof IntersectionObserver === 'undefined') {
    // 没有观察器就**立刻排**（老环境／测试环境）：宁可慢，也不能让公式永远不出现。
    container.querySelectorAll<HTMLElement>('[data-katex-lazy]').forEach(renderNow)

    return () => {}
  }

  const observer = new IntersectionObserver(
    entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const element = entry.target as HTMLElement
        observer.unobserve(element)
        renderNow(element)
      }
    },
    { rootMargin: ROOT_MARGIN }
  )

  const watch = (node: HTMLElement) => {
    node.querySelectorAll<HTMLElement>('[data-katex-lazy]').forEach(element => observer.observe(element))
  }

  watch(container)

  /**
   * Streamdown 是**按块**渲染的：占位可能在这一轮 effect 之后才进 DOM。
   * 没有这一层，它们就永远不会被观察 ⇒ 公式永远不排（屏上是一片空白）。
   */
  const mutations = typeof MutationObserver === 'undefined' ? null : new MutationObserver(records => {
    for (const record of records) {
      for (const node of Array.from(record.addedNodes)) {
        if (node instanceof HTMLElement) watch(node)
      }
    }
  })

  mutations?.observe(container, { childList: true, subtree: true })

  return () => {
    observer.disconnect()
    mutations?.disconnect()
  }
}
