import { useStore } from '@nanostores/react'

import { showsTransition, swapLabel } from '@/app/chat/agent-enter'
import { $agentEnter } from '@/app/chat/agent-enter-store'
import { $consoleAgents } from '@/app/console/store/agents'
import { TurtleLoader } from '@/components/ui/turtle-loader'
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
 * **加载画面单独显示**（用户 11-13 12:35：「应该是加载画面单独显示。而不是像现在
 * 这样叠加在切换前的聊天界面。重叠在字上面很丑」）：这一层现在是**不透明**的，
 * 切换时这张卡只剩加载画面——旧转录、`Entering …` 那句字，都不与旧正文叠在一起。
 * 底色是**独立的一层**，且只在真的在等／失败时渲染：若让它跟着外层那个
 * `opacity-0` 一起淡，空闲时整张卡会被糊成一片底色。
 *
 * 等待指示 = **品牌小乌龟沿一条跑道从左走到右**（`components/ui/turtle-loader`，
 * 主人 11-13 同一天拍的统一形状）。纯 CSS `transform` 走位，零逐帧 DOM 改写——
 * 上一刀 `WP-CHAT-CARD-FLICKER` 的病根正是那枚粒子 `Loader` 常驻一条 rAF 循环
 * （每帧重算 220 步路径 ＋ 改写 40 个粒子的属性，约 1.7 万次/秒）。
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

  const visible = busy || failed

  return (
    <div
      aria-hidden={!failed}
      className={cn(
        'pointer-events-none absolute inset-0 z-50 flex items-center justify-center transition-opacity duration-150 ease-out',
        visible ? 'opacity-100' : 'opacity-0'
      )}
      data-testid="chat-swap-overlay"
      role={failed ? 'alert' : undefined}
    >
      {/* 不透明底色：切换时这张卡**只**显示加载画面，旧转录一点都透不出来。 */}
      {visible ? (
        <div
          className="absolute inset-0 bg-(--ui-chat-surface-background)"
          data-testid="chat-swap-overlay-backdrop"
        />
      ) : null}
      <div className="relative flex flex-col items-center gap-3">
        {/* 那枚乌龟**只在真的在等**的时候才挂：不在等的时候挂着它，就是一张
            对话卡常驻一条动画循环（上一刀那场「一直在闪」的病根）。 */}
        {busy ? (
          <TurtleLoader aria-hidden="true" role="presentation" size="md" />
        ) : null}
        <span className="max-w-[22rem] text-center text-[0.8125rem] text-(--ui-text-tertiary)">{text}</span>
      </div>
    </div>
  )
}
