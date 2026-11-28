/**
 * ``plobi-plugin://`` 这个 scheme 的**接线**（深档机制第三刀）。
 *
 * 纯逻辑在 ``plugin-protocol.ts``（不引 electron，可被 ``node:test`` 直接测）；
 * 这一份只做两件事：**在 app ready 之前**把 scheme 注册成 privileged ＋
 * **在 app ready 之后**把 handler 接上。
 */

import fs from 'node:fs'

import { protocol } from 'electron'

import {
  findPluginUiRoot,
  PLUGIN_PROTOCOL,
  pluginAssetCsp,
  pluginProtocolPrivileges,
  resolvePluginAsset
} from './plugin-protocol'

/** **必须在 app ready 之前**调一次（Electron 的规矩：scheme 特权只在 ready 前能注册）。 */
export function registerPluginScheme(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: PLUGIN_PROTOCOL,
      privileges: pluginProtocolPrivileges() as never
    }
  ])
}

/** 把 ``plobi-plugin://`` 接上（**app ready 之后**调一次）。 */
export function registerPluginProtocol(roots: readonly string[]): void {
  protocol.handle(PLUGIN_PROTOCOL, async request => {
    try {
      const url = new URL(request.url)
      const dir = decodeURIComponent(url.hostname || '')
      const uiRoot = findPluginUiRoot(roots, dir)

      if (!uiRoot) {
        return new Response('Plugin UI not found', { status: 404 })
      }

      const asset = resolvePluginAsset(uiRoot, url.pathname)

      if (!asset) {
        return new Response('Not found', { status: 404 })
      }

      const body = await fs.promises.readFile(asset.filePath)

      return new Response(body, {
        status: 200,
        headers: {
          'Content-Type': asset.contentType,
          'Content-Security-Policy': pluginAssetCsp(),
          'X-Content-Type-Options': 'nosniff'
        }
      })
    } catch {
      return new Response('Plugin UI error', { status: 500 })
    }
  })
}
