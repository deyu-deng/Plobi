/**
 * 切换 Agent / 会话时中间栏那一段的过渡（2026-10-21 用户反馈：「切换 Agents
 * 聊天界面的时候，中间没有一个动画过渡态，给用户卡顿的体验」）。
 *
 * 病根是**中间栏的内容是硬切换**：换 Agent 时旧转录被丢掉、新转录直接顶上，
 * 而 `ChatSwapOverlay` 只盖住「正在进入」那一段（且它盖的是遮罩，不是内容）
 * ——于是用户看到的是「空白—一闪—又空白」，感知就是卡。
 *
 * 这里只管**新内容的入场**（退场由挂载/卸载负责，重挂载会把转录也重挂一次，
 * 不能碰）。动效刻意做得很轻：淡入 + 6px 上移，180ms，走 Web Animations API
 * ——不重挂子树、不动 React 树、也不进 CSS 关键帧表（那个表已经背着
 * `thread-jump-*` 那一族了）。
 *
 * `prefers-reduced-motion: reduce` 时**不做任何动效**（返回 null）：系统说了
 * 别动，就别动——不是改成「更短的动效」。
 */

export const THREAD_SWAP_DURATION_MS = 180

export interface ThreadSwapAnimation {
  keyframes: Keyframe[]
  options: KeyframeAnimationOptions
}

/**
 * 线程区入场动效。`reducedMotion` 为真 ⇒ 没有动效（null）。
 *
 * 纯函数（不碰 DOM），这样它能被单测钉住——这条契约是「切换时有一次淡入，
 * 且尊重系统的减弱动效设置」。
 */
export function threadSwapAnimation(reducedMotion: boolean): ThreadSwapAnimation | null {
  if (reducedMotion) {
    return null
  }

  return {
    keyframes: [
      { opacity: 0, transform: 'translateY(6px)' },
      { opacity: 1, transform: 'translateY(0)' }
    ],
    options: {
      duration: THREAD_SWAP_DURATION_MS,
      easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)'
    }
  }
}

/** 系统是否要求减弱动效。SSR/测试环境没有 matchMedia 时按「不动」处理。 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true
  }

  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return true
  }
}
