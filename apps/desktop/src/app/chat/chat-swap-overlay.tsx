import { useStore } from '@nanostores/react'

import { showsTransition, swapLabel } from '@/app/chat/agent-enter'
import { $agentEnter } from '@/app/chat/agent-enter-store'
import { $consoleAgents } from '@/app/console/store/agents'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

/**
 * 中间栏那一段过渡（`WP-RAIL-SWITCH-LOADING`）。
 *
 * 病根是覆盖区间：进一个 L2 要等三段（概览 → 起/绑后端 → 会话列表与转录），
 * 而原来只有第二段有节点 ⇒ 用户看到「空白—一闪—又空白」。现在进入态由控制器
 * **在点下去那一刻就点亮**，整段都在场上；失败**不再转圈**——那时转圈就是
 * "看起来像加载中"那种骗，给的是那一句原因。
 *
 * 等待指示 = **Capabilities 页那枚现成的**（`components/ui/loader` 的
 * `rose-curve`：粒子拖尾沿一条 5 瓣玫瑰线跑，整体缓慢自转 + 呼吸）。用户
 * 10-22：「加载态有现成的呀。我截图的就是 capacity 的加载界面的动画。你可以
 * 使用一下」——所以这里不自己画一枚，直接用 `PageLoader` 用的同一组参数
 * （它要占满一页，这里只需要中间那一小枚）。
 */
export function ChatSwapOverlay({ profile }: { profile: string | null }) {
  const { t } = useI18n()
  const enter = useStore($agentEnter)
  const agents = useStore($consoleAgents)

  const failed = enter.status === 'failed'
  const transitioning = showsTransition(enter)
  const busy = !failed && (transitioning || Boolean(profile))
  const name = enter.status === 'entering' ? enter.label : swapLabel(agents, profile)

  const text = failed
    ? `${enter.reasonKey ? t.console[enter.reasonKey] : t.console.agentEnterNeutral}${enter.detail}`
    : name
      ? t.console.enteringAgent(name)
      : t.console.agentEnterNeutral

  return (
    <div
      aria-hidden={!failed}
      className={cn(
        'pointer-events-none absolute inset-0 z-50 flex items-center justify-center transition-opacity duration-150 ease-out',
        busy || failed ? 'opacity-100' : 'opacity-0'
      )}
      data-testid="chat-swap-overlay"
      role={failed ? 'alert' : undefined}
    >
      <div className="flex flex-col items-center gap-3">
        {failed
          ? null
          : (
              <Loader
                aria-hidden="true"
                className="size-10 text-primary/70"
                pathSteps={220}
                role="presentation"
                strokeScale={0.72}
                type="rose-curve"
              />
            )}
        <span className="max-w-[22rem] text-center text-[0.8125rem] text-(--ui-text-tertiary)">{text}</span>
      </div>
    </div>
  )
}
