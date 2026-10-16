import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { showsTransition, swapLabel } from '@/app/chat/agent-enter'
import { $agentEnter } from '@/app/chat/agent-enter-store'
import { $consoleAgents } from '@/app/console/store/agents'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

// Braille spinner frames — reads as a tiny ASCII loader in monospace.
const FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

/**
 * 中间栏那一段过渡（`WP-RAIL-SWITCH-LOADING`）。
 *
 * 病根是覆盖区间：进一个 L2 要等三段（概览 → 起/绑后端 → 会话列表与转录），
 * 而原来只有第二段有节点 ⇒ 用户看到「空白—一闪—又空白」。现在进入态由控制器
 * **在点下去那一刻就点亮**，整段都在场上；失败**不再转圈**——那时转圈就是
 * "看起来像加载中"那种骗，给的是那一句原因。
 */
export function ChatSwapOverlay({ profile }: { profile: string | null }) {
  const { t } = useI18n()
  const enter = useStore($agentEnter)
  const agents = useStore($consoleAgents)
  const [frame, setFrame] = useState(0)

  const failed = enter.status === 'failed'
  const transitioning = showsTransition(enter)
  const busy = !failed && (transitioning || Boolean(profile))
  const name = enter.status === 'entering' ? enter.label : swapLabel(agents, profile)

  useEffect(() => {
    if (!busy) {
      return
    }

    const id = window.setInterval(() => setFrame(value => (value + 1) % FRAMES.length), 80)

    return () => window.clearInterval(id)
  }, [busy])

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
      <div className="flex items-center gap-2 bg-[color-mix(in_srgb,var(--dt-card)_92%,transparent)] px-4 py-2 font-mono text-[0.8125rem] text-foreground shadow-composer">
        {failed
          ? null
          : <span className="w-3 text-(--ui-accent)">{FRAMES[frame]}</span>}
        <span>{text}</span>
      </div>
    </div>
  )
}
