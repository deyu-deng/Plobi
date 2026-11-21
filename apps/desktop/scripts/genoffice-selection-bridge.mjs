/**
 * GenOffice webview 选区桥（WP-OFFICE-SELECTION-TOOLBAR 阶段二）。
 *
 * Word/PPT/Excel 编辑器的内容活在一个独立渲染进程（<webview>）里，宿主的
 * `window.getSelection()` 够不到那里的选区。所以在 preload（isolated world，
 * 共享 webview 的 DOM）里装一枚监听：人一放开鼠标，就把选中的字与它相对
 * webview 视口的矩形经 `ipcRenderer.sendToHost` 发给宿主；宿主换算成主页面
 * 坐标后喂给那枚共享的 SelectionToolbar（external 模式）。
 *
 * 不改 GenOffice 的业务逻辑，只在它三个 preload 源码尾部追加一段自包含代码。
 * 用 needle 断言确认注入点还在——上游升级改了 preload 形状就让构建当场红，
 * 不静默失效（同 docx-prefix-tolerance 的规矩）。
 */

/** 发给宿主的通道名。宿主侧 genoffice-editor.tsx 认这个。 */
export const SELECTION_CHANNEL = 'plobi:selection'

/**
 * 注入进 preload 的运行时源码。自包含：只用 `ipcRenderer`（preload 顶部已 import）
 * 与 DOM API，不引用本文件的任何模块级常量。
 */
export const SELECTION_BRIDGE_SOURCE = `

// ── Plobi: 选区上报（见 apps/desktop/scripts/genoffice-selection-bridge.mjs）──
{
  let __plobiSelectionTimer = 0
  const report = () => {
    try {
      const sel = window.getSelection()
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
        ipcRenderer.sendToHost('${SELECTION_CHANNEL}', null)
        return
      }
      const text = (sel.toString() || '').trim()
      if (!text) {
        ipcRenderer.sendToHost('${SELECTION_CHANNEL}', null)
        return
      }
      const rect = sel.getRangeAt(0).getBoundingClientRect()
      ipcRenderer.sendToHost('${SELECTION_CHANNEL}', {
        text,
        rect: { bottom: rect.bottom, left: rect.left, right: rect.right, top: rect.top }
      })
    } catch {
      // 选区桥只是便利，任何失败都不该影响编辑器本身
    }
  }
  document.addEventListener('mouseup', () => {
    window.clearTimeout(__plobiSelectionTimer)
    __plobiSelectionTimer = window.setTimeout(report, 30)
  })
  document.addEventListener('keyup', event => {
    if (event.shiftKey || event.key === 'Shift') {
      window.clearTimeout(__plobiSelectionTimer)
      __plobiSelectionTimer = window.setTimeout(report, 30)
    }
  })
}
`

/**
 * 在一段 preload 源码尾部追加选区桥。找不到 sendToHost 或已注入过就抛错——
 * 前者说明上游改了形状，后者说明重复注入（都可能让行为异常）。
 */
export function injectSelectionBridge(source) {
  if (typeof source !== 'string') {
    throw new Error('genoffice selection bridge: source is not a string')
  }
  if (source.includes(SELECTION_CHANNEL)) {
    throw new Error(
      'genoffice selection bridge: already injected — check the build pipeline for a double plugin'
    )
  }
  const needles = source.split('ipcRenderer.sendToHost').length - 1
  if (needles < 1) {
    throw new Error(
      'genoffice selection bridge: no `ipcRenderer.sendToHost` in preload — upstream shape changed, re-check the injection point before rebuilding'
    )
  }
  return `${source.trimEnd()}\n${SELECTION_BRIDGE_SOURCE}\n`
}

/**
 * esbuild onLoad 插件：对三个 preload 源码注入选区桥。
 * filter 只认 `*-preload.ts`，其余文件原样返回。
 */
export const genofficeSelectionBridgePlugin = {
  name: 'genoffice-selection-bridge',
  setup(build) {
    build.onLoad({ filter: /preload\.ts$/ }, async args => {
      const fs = await import('node:fs/promises')
      const source = await fs.readFile(args.path, 'utf8')
      return { contents: injectSelectionBridge(source), loader: 'ts' }
    })
  }
}
