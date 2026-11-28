import { useCallback, useEffect, useRef } from 'react'

/**
 * 插件的界面跑在**独立 origin** 的 iframe 里（深档机制第三刀 · 规格
 * ``Docs/specs/plugin-sandbox.md`` §2.3）：``plobi-plugin://<插件目录名>/<入口>``。
 *
 * 三条规矩（都落在这一个组件里）：
 *
 * 1. **``sandbox="allow-scripts"``** ⇒ iframe 里没有同源特权（**不与宿主共享 DOM**），
 *    也没有 ``allow-same-origin``（拿不到宿主那个 origin）；
 * 2. **唯一的通道是 postMessage** ⇒ 宿主只认**自己那个 iframe 窗口**发来的消息
 *    （``event.source`` 对得上），别的窗口发的一律不理；
 * 3. 消息**带 schema**（``type`` 字段）⇒ 认不出的形状当场丢掉。
 */

export const PLUGIN_PROTOCOL = 'plobi-plugin'

/** 插件界面发来的消息的形状（宿主只认这一种）。 */
export interface PluginMessage {
  /** 消息的种类（宿主自己那套协议；插件发别的形状⇒ 不认）。 */
  type: string
  /** 一次请求的编号（宿主回话时原样带回）。 */
  id?: string
  /** 载荷（宿主自己解析；组件只保证它是 JSON 形状）。 */
  payload?: unknown
}

const KNOWN_MESSAGE_TYPES = new Set(['plobi:ready', 'plobi:request', 'plobi:event'])

/**
 * 校验一条消息的形状（**认不出就丢掉**，不猜、不放行）。
 *
 * `event.origin` 在 ``sandbox`` 且没有 ``allow-same-origin`` 时是 ``"null"``
 * ⇒ **来源只能靠 ``event.source`` 对窗口**（那才是硬的）。
 */
export function parsePluginMessage(data: unknown): PluginMessage | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return null
  }

  const candidate = data as Record<string, unknown>
  const type = candidate.type

  if (typeof type !== 'string' || !KNOWN_MESSAGE_TYPES.has(type)) {
    return null
  }

  const message: PluginMessage = { type }

  if (typeof candidate.id === 'string') {
    message.id = candidate.id
  }

  if ('payload' in candidate) {
    message.payload = candidate.payload
  }

  return message
}

/** 插件界面的落点（`dir`＝插件目录名；`entry`＝那一格里相对入口）。 */
export function pluginFrameSrc(dir: string, entry = 'index.html'): string {
  return `${PLUGIN_PROTOCOL}://${dir}/${entry.replace(/^\/+/, '')}`
}

export function PluginFrame({
  dir,
  entry = 'index.html',
  onMessage,
  title
}: {
  dir: string
  entry?: string
  /** 插件发来一条**认得的**消息时（已经过了来源与形状两道）。 */
  onMessage?: (message: PluginMessage) => void
  title?: string
}) {
  const frameRef = useRef<HTMLIFrameElement | null>(null)

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      // 来源核对：**只认自己那个 iframe 窗口**（origin 在 sandbox 下是 "null"，靠不住）。
      const frame = frameRef.current

      if (!frame || event.source !== frame.contentWindow) {
        return
      }

      const message = parsePluginMessage(event.data)

      if (!message) {
        return
      }

      onMessage?.(message)
    },
    [onMessage]
  )

  useEffect(() => {
    window.addEventListener('message', handleMessage)

    return () => {
      window.removeEventListener('message', handleMessage)
    }
  }, [handleMessage])

  return (
    <iframe
      className="size-full border-0 bg-transparent"
      data-plugin-frame={dir}
      // `allow-scripts` 而**没有** `allow-same-origin` ⇒ iframe 里没有同源特权。
      sandbox="allow-scripts"
      src={pluginFrameSrc(dir, entry)}
      title={title ?? dir}
    />
  )
}
