/**
 * 插件界面的**独立 origin**（深档机制第三刀 · 规格 ``Docs/specs/plugin-sandbox.md`` §2.3）。
 *
 * 插件的界面（它的 ``ui/`` 静态产物）跑在 **``plobi-plugin://<插件目录名>/``** 这个
 * **独立 origin** 里 ⇒ 它与宿主 UI **不共享 DOM**、碰不到宿主内存；它与宿主之间
 * **唯一的通道是 postMessage**（结构化、带 schema、**校验来源**）。
 *
 * 三条硬规矩（抄 Chrome MV3 的扩展页那一套）：
 *
 * 1. **不可放宽的 CSP**：脚本只准同源文件（**不许 inline script／unsafe-eval／远程脚本**），
 *    连接一律断掉（``connect-src 'none'``）——插件要拿数据只能问宿主。
 * 2. **路径不许跑出 ``ui/`` 那一格**：词法越界（``..``）与**软链越界**都拒。
 * 3. **只给静态产物**：白名单里的那几种类型才发（不认识的类型 ⇒ 404）。
 *
 * ⚠️ **照实说**：这是 **origin 隔离 ＋ CSP ＋ 权限面**，**不是进程隔离**（同一个渲染
 * 进程 ⇒ 插件那段 JS 崩了可能带走这一页）⇒ 进程级隔离的升级路留在 ``WebContentsView``。
 *
 * 这一份**不引 electron**（纯逻辑，可被 ``node:test`` 直接测）；接线在
 * ``plugin-protocol-wiring.ts``。
 */

import fs from 'node:fs'
import path from 'node:path'

/** 插件界面的 scheme（独立 origin 的门牌）。 */
export const PLUGIN_PROTOCOL = 'plobi-plugin'

/** 插件目录里放界面产物的那一格（``<插件目录>/ui/``）。 */
export const PLUGIN_UI_SUBDIR = 'ui'

/** scheme 的特权（**必须在 app ready 之前**注册）。 */
export function pluginProtocolPrivileges(): Record<string, boolean> {
  return {
    secure: true,
    standard: true,
    supportFetchAPI: true,
    corsEnabled: true,
    stream: true
  }
}

/**
 * 插件界面自己那份 CSP（**不可放宽**）。
 *
 * - ``script-src 'self'`` ⇒ 脚本只准同源文件（inline script 与 eval 都不行）；
 * - ``connect-src 'none'`` ⇒ 插件的界面**不许自己联网**（要数据只能问宿主）；
 * - ``frame-ancestors 'self'`` ⇒ 只准被宿主那一面装进 iframe。
 */
export function pluginAssetCsp(): string {
  return [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'none'",
    "form-action 'none'",
    "base-uri 'none'",
    "frame-ancestors 'self'"
  ].join('; ')
}

const CONTENT_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8'
}

/**
 * 请求的那条路径落在**哪个文件**上（**不许跑出 ``ui/`` 那一格**）。
 *
 * 越界两种，都拒：
 *
 * - **词法越界**：``..``／绝对路径 ⇒ 解析后不在 ``ui/`` 里面；
 * - **软链越界**：``ui/`` 里一个软链指着外面 ⇒ **解析软链之后**再看一遍。
 */
export function resolvePluginAsset(
  uiRoot: string,
  requestPath: string
): { filePath: string; contentType: string } | null {
  let decoded = ''

  try {
    decoded = decodeURIComponent(requestPath || '')
  } catch {
    return null
  }

  decoded = decoded.replace(/^[\\/]+/, '')

  if (decoded.includes('\0')) {
    return null
  }

  // 目录（末尾带 / 或就是空）⇒ 那一份的 index.html
  if (!decoded || decoded.endsWith('/')) {
    decoded = `${decoded}index.html`
  }

  const root = path.resolve(uiRoot)
  const target = path.resolve(root, decoded)

  if (target !== root && !target.startsWith(root + path.sep)) {
    return null
  }

  const contentType = CONTENT_TYPES[path.extname(target).toLowerCase()]

  if (!contentType) {
    return null
  }

  // 软链也要看：``resolve`` 不跟软链走 ⇒ 这里再解析一次真实路径。
  let realRoot = ''
  let realTarget = ''

  try {
    realRoot = fs.realpathSync(root)
    realTarget = fs.realpathSync(target)
  } catch {
    return null
  }

  if (realTarget !== realRoot && !realTarget.startsWith(realRoot + path.sep)) {
    return null
  }

  return { filePath: realTarget, contentType }
}

/** 那个插件的界面产物在哪一格（按 roots 顺序找；找不到就没有）。 */
export function findPluginUiRoot(roots: readonly string[], dir: string): string | null {
  if (!dir || dir.includes('/') || dir.includes('\\')) {
    return null
  }

  for (const root of roots) {
    const candidate = path.join(path.resolve(root), dir, PLUGIN_UI_SUBDIR)

    try {
      if (fs.statSync(candidate).isDirectory()) {
        return candidate
      }
    } catch {
      // 不是目录（或不存在）⇒ 换下一个 root
    }
  }

  return null
}
