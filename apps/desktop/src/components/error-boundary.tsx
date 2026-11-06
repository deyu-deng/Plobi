import { Component, type CSSProperties, type ErrorInfo, type ReactNode } from 'react'

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
 * 实测咬过三次（`$scopeAgent` / `AgentActionsMenu` /
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

const FALLBACK_BTN: CSSProperties = {
  padding: '6px 14px',
  borderRadius: 8,
  border: '1px solid #3a3f4b',
  background: '#242833',
  color: '#e6e7ea',
  cursor: 'pointer',
  font: 'inherit'
}

/**
 * 根错误页**不许再依赖任何会被同一次热更弄坏的模块**（用户 10-25 白屏事故）。
 *
 * 上一版这里用了 `useI18n()` + `ErrorState` + `Button`。当悬空引用正好落在 i18n
 * 那条链上时（今天就是：`NotebookTabs is not defined` 之后 i18n 也进了坏状态），
 * **错误页自己也抛** ⇒ React 卸掉整棵树 ⇒ 用户看到纯白屏、连"重载"按钮都没有。
 * 所以这一页只用裸 HTML + 内联样式：错误页是整个界面的最后一道网，它自己不能有依赖。
 */
function RootErrorFallback({ error, reset }: ErrorBoundaryFallbackProps) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1500,
        display: 'grid',
        placeItems: 'center',
        padding: 24,
        background: '#16181d',
        color: '#e6e7ea',
        font: "13px/1.6 system-ui, -apple-system, 'Segoe UI', sans-serif"
      }}
    >
      <div style={{ width: '100%', maxWidth: '34rem' }}>
        <div style={{ marginBottom: 8, fontSize: 16, fontWeight: 600 }}>
          界面出错了 · Something went wrong
        </div>
        <pre
          style={{
            margin: '0 0 14px',
            maxHeight: '16rem',
            overflow: 'auto',
            padding: 12,
            border: '1px solid #2c303a',
            borderRadius: 8,
            background: '#1f2229',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word'
          }}
        >
          {error.message}
        </pre>
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={reset} style={FALLBACK_BTN} type="button">
            重试 · Retry
          </button>
          <button onClick={() => window.location.reload()} style={FALLBACK_BTN} type="button">
            重载窗口 · Reload
          </button>
          <button
            onClick={() => void window.plobiDesktop?.revealLogs?.()?.catch(() => undefined)}
            style={{ ...FALLBACK_BTN, background: 'transparent' }}
            type="button"
          >
            打开日志 · Logs
          </button>
        </div>
      </div>
    </div>
  )
}
