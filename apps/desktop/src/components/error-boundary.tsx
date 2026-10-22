import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'

export interface ErrorBoundaryFallbackProps {
  error: Error
  reset: () => void
}

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: (props: ErrorBoundaryFallbackProps) => ReactNode
  label?: string
  onError?: (error: Error, info: ErrorInfo) => void
}

interface ErrorBoundaryState {
  error: Error | null
}

/** 自动重载一次的门闩（sessionStorage：同一個窗口会话内有效，重开窗口即重置）。 */
const HMR_RELOAD_ONCE_KEY = 'plobi:hmr-stale-reload-once'

/**
 * 这是「模块图被热更新搞坏了」的特征，不是代码 bug。
 *
 * Vite + React Fast Refresh 在**跨多个文件**的热更之后，偶发会留下悬空绑定：
 * 渲染时报 ``X is not defined``，而 X 明明就在源文件里 import 着。本机 dev 上
 * 实测咬过三次（`$activeGatewayProfile` / `AgentActionsMenu` /
 * `AGENT_ICON_CHOICES`，全在 2026-10-21 晚间的十几轮 HMR 之后）。
 *
 * 这种错 React 的错误边界救不了：它能重建组件树，但救不活已经失效的模块绑定
 * ——表现为"界面看着正常、点哪儿都没反应"（用户 10-22 报的就是这个）。
 * 唯一的修法是整窗重载。与其等用户自己发现，这里自动做，但**只做一次**
 * （标记在 sessionStorage 里；同一窗口若重载后还报，就交回给错误页，不循环）。
 */
function isStaleHotUpdateReference(error: Error): boolean {
  return error instanceof ReferenceError && /\b[A-Za-z_$][\w$]* is not defined\b/.test(error.message)
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const tag = this.props.label ? `[error-boundary:${this.props.label}]` : '[error-boundary]'
    console.error(tag, error, info.componentStack)
    this.props.onError?.(error, info)

    if (!isStaleHotUpdateReference(error)) {
      return
    }

    let alreadyRetried = false

    try {
      alreadyRetried = window.sessionStorage.getItem(HMR_RELOAD_ONCE_KEY) === '1'

      if (!alreadyRetried) {
        window.sessionStorage.setItem(HMR_RELOAD_ONCE_KEY, '1')
      }
    } catch {
      // sessionStorage 不可用（隐私模式等）⇒ 不自动重载，交给错误页上的按钮。
      return
    }

    if (!alreadyRetried) {
      console.warn('[error-boundary] 模块图被热更新搞坏了，整窗重载一次自愈')
      window.location.reload()
    }
  }

  componentDidMount() {
    this.clearHmrLatch()
  }

  componentDidUpdate() {
    this.clearHmrLatch()
  }

  /** 渲染成功了就撤掉门闩——下次真坏时还允许再自愈一次。 */
  private clearHmrLatch() {
    if (this.state.error) {
      return
    }

    try {
      window.sessionStorage.removeItem(HMR_RELOAD_ONCE_KEY)
    } catch {
      /* 擦不掉不影响什么 */
    }
  }

  reset = () => {
    this.setState({ error: null })
  }

  render() {
    const { error } = this.state

    if (!error) {
      return this.props.children
    }

    if (this.props.fallback) {
      return this.props.fallback({ error, reset: this.reset })
    }

    return <RootErrorFallback error={error} reset={this.reset} />
  }
}

function RootErrorFallback({ error, reset }: ErrorBoundaryFallbackProps) {
  const { t } = useI18n()

  return (
    <div className="fixed inset-0 z-[1500] grid place-items-center bg-(--ui-chat-surface-background) p-6">
      <ErrorState
        className="w-full max-w-[28rem]"
        description={error.message || t.errors.boundaryDesc}
        title={t.errors.boundaryTitle}
      >
        <Button className="font-semibold" onClick={reset} size="lg">
          {t.common.retry}
        </Button>
        <Button onClick={() => window.location.reload()} variant="text">
          {t.errors.reloadWindow}
        </Button>
        <Button onClick={() => void window.plobiDesktop?.revealLogs()?.catch(() => undefined)} variant="text">
          {t.errors.openLogs}
        </Button>
      </ErrorState>
    </div>
  )
}
