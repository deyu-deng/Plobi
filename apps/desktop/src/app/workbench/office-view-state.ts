/**
 * Office 预览的阅读位置记忆。
 *
 * webview 里的 GenOffice 引擎（word / sheets / slides）是打包好的第三方渲染器，
 * 宿主侧拿不到它的编辑器实例，也没有任何 viewState 之类的公开接口。所以这里把
 * 一小段自包含脚本注入 webview：脚本自己找滚动容器、按滚动比例把位置写进
 * webview 的 localStorage（partition 带 persist: 前缀，重启后还在），下次打开
 * 同一个文件时再按比例还原。
 *
 * 只存比例而不是像素：文档重排、窗口缩放、缩放档位变化之后像素必然漂，比例
 * 不会。引擎的渲染是分批的（首帧 scrollHeight 往往只有最终的一小半），所以还原
 * 放在一个时间窗口里反复校正，一旦用户自己滚动/按键就立刻交还控制权。
 */

export interface OfficeViewSnapshot {
  ratio: number
  at: number
}

/** webview 内 localStorage 的键前缀，改它等于让所有人的历史位置失效。 */
export const OFFICE_VIEW_STATE_KEY_PREFIX = 'plobi.office.view-state.v1'

/** 还原窗口：引擎分批渲染，这段时间内反复把位置往目标比例上拉。 */
const RESTORE_WINDOW_MS = 15_000

/** 保存节流：滚动过程中不要每帧写 localStorage。 */
const SAVE_THROTTLE_MS = 400

/** 可滚动判据下限，避免把 1~2px 的伪滚动容器当成文档。 */
const MIN_SCROLL_ROOM = 24

export function officeViewStateKey(kind: string, path: string): string {
  return `${OFFICE_VIEW_STATE_KEY_PREFIX}:${kind}:${path}`
}

/** localStorage 里的原始值可能是旧版本、被人手改过、或整个 JSON 坏掉。 */
export function parseOfficeViewSnapshot(raw: unknown): OfficeViewSnapshot | null {
  if (typeof raw !== 'string' || raw === '') {
    return null
  }

  let parsed: unknown

  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }

  if (!parsed || typeof parsed !== 'object') {
    return null
  }

  const source = parsed as { at?: unknown; ratio?: unknown }
  const ratio = source.ratio

  if (typeof ratio !== 'number' || !Number.isFinite(ratio) || ratio < 0 || ratio > 1) {
    return null
  }

  return {
    ratio,
    at: typeof source.at === 'number' && Number.isFinite(source.at) ? source.at : 0
  }
}

/**
 * 生成注入 webview 的自包含脚本。
 *
 * 只能用字符串模板：executeJavaScript 在 webview 的渲染进程里跑，拿不到本模块的
 * 任何运行时导出，所以键名、阈值这些常量必须在这里拼进去。
 */
export function officeViewStateScript({ kind, path }: { kind: string; path: string }): string {
  const key = JSON.stringify(officeViewStateKey(kind, path))

  return `(() => {
  const KEY = ${key}
  const RESTORE_WINDOW_MS = ${RESTORE_WINDOW_MS}
  const SAVE_THROTTLE_MS = ${SAVE_THROTTLE_MS}
  const MIN_ROOM = ${MIN_SCROLL_ROOM}

  if (window.__plobiOfficeViewState === KEY) {
    return
  }

  window.__plobiOfficeViewState = KEY

  const readSaved = () => {
    try {
      const parsed = JSON.parse(localStorage.getItem(KEY) || 'null')

      if (parsed && typeof parsed.ratio === 'number' && parsed.ratio >= 0 && parsed.ratio <= 1) {
        return parsed.ratio
      }
    } catch {
      /* 历史值坏掉就当没存过 */
    }

    return null
  }

  const roomOf = el => (el ? el.scrollHeight - el.clientHeight : 0)

  const looksScrollable = el => {
    if (!el || !el.isConnected) {
      return false
    }

    const overflowY = getComputedStyle(el).overflowY

    return (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') && roomOf(el) >= MIN_ROOM
  }

  let cached = null

  const findScroller = () => {
    if (looksScrollable(cached)) {
      return cached
    }

    cached = null

    /* Word 的正文滚动容器固定是 .editor-scroll（引擎自己也在用它算页宽）。 */
    const preferred = document.querySelector('.editor-scroll')

    if (looksScrollable(preferred)) {
      cached = preferred

      return cached
    }

    /* sheets / slides 的容器类名不对外承诺，按「可视面积最大的可滚动块」认。 */
    let best = null
    let bestArea = 0
    const nodes = document.querySelectorAll('div, main, section, [role="main"]')

    for (const el of nodes) {
      if (!looksScrollable(el)) {
        continue
      }

      const area = el.clientWidth * el.clientHeight

      if (area > bestArea) {
        bestArea = area
        best = el
      }
    }

    cached = best

    return cached
  }

  const save = () => {
    const el = findScroller()
    const room = roomOf(el)

    if (!el || room < MIN_ROOM) {
      return
    }

    const ratio = Math.min(1, Math.max(0, el.scrollTop / room))

    try {
      localStorage.setItem(KEY, JSON.stringify({ at: Date.now(), ratio }))
    } catch {
      /* 配额满了就算了，位置不是数据 */
    }
  }

  let saveTimer = 0

  const onScroll = () => {
    if (restoring) {
      return
    }

    if (saveTimer) {
      return
    }

    saveTimer = window.setTimeout(() => {
      saveTimer = 0
      save()
    }, SAVE_THROTTLE_MS)
  }

  let restoring = false
  let userTookOver = false
  let stableHits = 0

  const stopRestoring = () => {
    restoring = false
    userTookOver = true
  }

  const onUserIntent = () => {
    if (restoring) {
      stopRestoring()
    }
  }

  const saved = readSaved()

  if (saved === null) {
    document.addEventListener('scroll', onScroll, true)
    window.addEventListener('pagehide', save)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        save()
      }
    })

    return
  }

  restoring = true

  const startedAt = Date.now()

  const step = () => {
    if (userTookOver) {
      return
    }

    const el = findScroller()
    const room = roomOf(el)

    if (el && room >= MIN_ROOM) {
      const want = saved * room

      if (Math.abs(el.scrollTop - want) <= 2) {
        stableHits += 1
      } else {
        el.scrollTop = want
        stableHits = 0
      }
    }

    const elapsed = Date.now() - startedAt

    /* 位置连续两拍都吻合说明这一批渲染的版式已经定了；时间窗用完则就地收手。 */
    if ((stableHits >= 2 && elapsed > 600) || elapsed > RESTORE_WINDOW_MS) {
      restoring = false

      return
    }

    window.setTimeout(step, 120)
  }

  window.addEventListener('wheel', onUserIntent, { passive: true })
  window.addEventListener('keydown', onUserIntent, true)
  window.addEventListener('pointerdown', onUserIntent, true)
  document.addEventListener('scroll', onScroll, true)
  window.addEventListener('pagehide', save)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      save()
    }
  })

  window.setTimeout(step, 120)
})()`
}

interface ViewStateWebview {
  executeJavaScript?: (code: string) => Promise<unknown>
  isConnected?: boolean
}

/**
 * 把位置记忆脚本挂到刚载入完的 webview 上。
 *
 * 注入失败不影响预览本身（比如 webview 还没拿到渲染进程），所以一律吞掉异常。
 */
export function applyOfficeViewState(webview: ViewStateWebview | null | undefined, kind: string, path: string): void {
  if (!webview || !path || typeof webview.executeJavaScript !== 'function') {
    return
  }

  try {
    void Promise.resolve(webview.executeJavaScript(officeViewStateScript({ kind, path }))).catch(() => {})
  } catch {
    /* 同上：位置记忆是锦上添花，不能把预览拖下水 */
  }
}
