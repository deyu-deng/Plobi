/**
 * The desktop's OWN traffic, from the same table everything else uses (WP-EGRESS-ROUTING).
 *
 * Why this is a separate rendering and not a second decision: the machine-wide takeover writes a
 * PAC for WinINET, but `session.setProxy` does not execute our PAC — it takes either a PAC URL of
 * its own or a fixed rule list. So the backend says the answer once (`network.egress` →
 * `agent/egress/system_proxy.desktop_view`, exposed on `/api/network/status` as `desktop_proxy`)
 * and this file only translates it into Chromium's dialect. 裁定 94: one reading, several
 * renderings — nothing here re-reads `network.egress` and nothing here decides routing.
 *
 * "那三档" = the lanes that reach the network on their own: the default session (the window),
 * `persist:plobi-embed` (embed previews), `persist:plobi-remote-oauth` (remote login), plus the
 * link-title fetcher's session. Setting only `defaultSession` would leave the other three on the
 * machine's settings, which is exactly the "配了不生效" shape this WP keeps being called out for.
 *
 * Chromium's bypass list is richer than WinINET's `ProxyOverride` (it takes CIDR and IPv6), so the
 * list arrives complete; anything the backend could not say comes back in `unexpressible` and is
 * logged rather than dropped.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

export interface DesktopProxyView {
  declared?: boolean
  mode?: string
  proxy?: string
  bypass?: string[]
  unexpressible?: string[]
  note?: string
}

export interface SessionProxyConfig {
  mode: 'system' | 'direct' | 'fixed_servers'
  proxyRules?: string
  proxyBypassRules?: string
}

/** A session-shaped object we can hand a proxy config to (`electron.Session` structural subset). */
export interface ProxyCapableSession {
  setProxy: (config: SessionProxyConfig) => unknown
}

/**
 * `system` while the table has not taken over (do not fight the user's own settings);
 * `fixed_servers` handing everything not-bypassed to our engine; and a missing or malformed
 * answer is ALSO `system` — a rendering that cannot read the table must not become an outage.
 */
export function desktopProxyConfig(view: DesktopProxyView | null | undefined): SessionProxyConfig {
  if (!view || typeof view !== 'object' || view.mode !== 'engine') {
    return { mode: 'system' }
  }

  const proxy = String(view.proxy || '').trim()

  if (!proxy) {
    return { mode: 'system' }
  }

  const bypass = (view.bypass ?? []).map((entry) => String(entry).trim()).filter(Boolean)

  return {
    mode: 'fixed_servers',
    proxyRules: proxy,
    ...(bypass.length ? { proxyBypassRules: bypass.join(';') } : {})
  }
}

/** One line for desktop.log: what we just told the sessions, in the backend's words. */
export function describeDesktopProxy(config: SessionProxyConfig, view: DesktopProxyView | null | undefined): string {
  if (config.mode !== 'fixed_servers') {
    return `desktop proxy: ${config.mode}（跟着系统设置；表没接管或读不出）`
  }

  const unspoken = (view?.unexpressible ?? []).length

  return [
    `desktop proxy: ${config.proxyRules}`,
    `bypass ${config.proxyBypassRules?.split(';').length ?? 0} 条`,
    unspoken ? `${unspoken} 条 direct 规则 Chromium 也说不清，交给引擎按同一张表判` : '',
    view?.note ? `（${view.note}）` : ''
  ].filter(Boolean).join(' · ')
}

/**
 * Apply to every lane. A session that throws (destroyed, or an Electron that dislikes a rule) is
 * named and skipped — losing one lane must not leave the call half-done and silent.
 */
export async function applyDesktopProxy(
  sessions: ProxyCapableSession[],
  view: DesktopProxyView | null | undefined
): Promise<{ config: SessionProxyConfig; applied: number; failed: string[] }> {
  const config = desktopProxyConfig(view)
  const failed: string[] = []
  let applied = 0

  for (const [index, entry] of sessions.entries()) {
    try {
      await entry.setProxy(config)
      applied += 1
    } catch (err) {
      failed.push(`session#${index}: ${String((err as Error)?.message || err)}`)
    }
  }

  return { config, applied, failed }
}

/**
 * The quit path's one job: hand the machine back to DIRECT (WP-EGRESS-ROUTING, 10-31).
 *
 * Why it lives in the desktop at all: on Windows `before-quit` hard-kills the backend tree
 * (`taskkill /T /F`), so the backend, the egress engine **and its watchdog** die in the same
 * instant — nobody gets to restore the system proxy, and the machine is left pointing at
 * `127.0.0.1:<mixed_port>` with nothing listening. Every app that reads the system proxy
 * (browsers, DingTalk, Marvis) then reads that as "no internet". The last code that still runs
 * before the tree-kill is this handler, so the hand-back happens here.
 *
 * Two limits, both load-bearing: only a **loopback** proxy is touched (a real remote proxy the
 * user configured is somebody else's answer, not ours to undo), and a registry we cannot read is
 * reported, not guessed — writing "off" because the query failed is how you turn an unreadable
 * machine into a machine with no route.
 */
const REG_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'
const LOOPBACK_PROXY = /^(?:https?:\/\/)?(?:127\.0\.0\.1|localhost|\[::1\]):\d+/i

/** The three seams this needs from the OS; a test injects a fake and never touches a registry. */
export interface ProxyRegistry {
  clear: (names: string[]) => void
  disable: () => void
  query: () => string
}

export const systemProxyRegistry: ProxyRegistry = {
  clear: names => {
    for (const name of names) {
      try {
        execFileSync('reg', ['delete', REG_KEY, '/v', name, '/f'], { stdio: 'ignore' })
      } catch {
        void 0 // a value that was never set is not a failure to hand back
      }
    }
  },
  disable: () => {
    execFileSync('reg', ['add', REG_KEY, '/v', 'ProxyEnable', '/t', 'REG_DWORD', '/d', '0', '/f'], { stdio: 'ignore' })
  },
  query: () => execFileSync('reg', ['query', REG_KEY], { encoding: 'utf8' })
}

export function readProxyValues(raw: string): { enable: boolean; server: string } {
  const enable = /ProxyEnable\s+REG_DWORD\s+0x1/i.test(raw)
  const match = raw.match(/ProxyServer\s+REG_SZ\s+(\S+)/)
  return { enable, server: match ? match[1] : '' }
}

/** What the quit handler decided, in one word — the log line carries the sentence. */
export type QuitProxyVerdict = 'unreadable' | 'not-taken-over' | 'not-ours' | 'handed-back'

export function restoreLoopbackSystemProxy(
  log: (line: string) => void,
  registry: ProxyRegistry = systemProxyRegistry
): QuitProxyVerdict {
  let raw: string

  try {
    raw = registry.query()
  } catch (err) {
    log(`[egress-proxy] quit: 读不到系统代理设置，所以什么都没改（${String((err as Error)?.message || err)}）`)
    return 'unreadable'
  }

  const said = readProxyValues(raw)
  if (!said.enable) {
    return 'not-taken-over'
  }
  if (!LOOPBACK_PROXY.test(said.server)) {
    // A proxy that is not loopback is somebody's own configuration. Handing it back would be us
    // deciding their network for them, and it is not what `ProxyEnable=1` alone ever proved.
    log(`[egress-proxy] quit: 系统代理是 ${said.server}（不是本机回环），不动它`)
    return 'not-ours'
  }

  registry.disable()
  registry.clear(['ProxyServer', 'AutoConfigURL', 'ProxyOverride'])
  log(`[egress-proxy] quit: 回环代理 ${said.server} → 交还直连，机器不至于跟着 App 一起没网`)
  return 'handed-back'
}

/**
 * WP-QUIT-CLEAN (11-05): the marker that tells the egress side "this one was a user quit".
 *
 * Measured 11-04/11-05: `before-quit` rewrote the registry, but nothing told the two *detached*
 * helpers — the perpetual guard (`agent/egress/proxy_guard.py`, pid 38384, still alive after the
 * app was gone, its pid file naming a dead pid) and the watchdog (`watchdog.py`, 20s tick whose
 * `taken-over` rule puts the takeover straight back). So the machine finished every session
 * pointing at a port nobody listened on.
 *
 * Why a marker instead of a kill from here: `taskkill` on a pid we cannot verify from Node is
 * exactly the bystander-kill `system_proxy._stop_guard` was hardened against (Windows recycles
 * pids, and the guard's own parent is an intentionally dead `cmd /c start` shim, so there is no
 * parent chain to check either). The desktop therefore states the *intent* with a timestamp, and
 * each helper retires itself — including its own pid file — on its next tick.
 */
export const QUIT_MARKER_NAME = 'user-quit.json'

/** Where the egress side keeps runtime state: `<PLOBI_HOME>/egress/runtime` (see `engine.egress_dir`). */
export function egressRuntimeDir(plobiHome: string): string {
  return path.join(plobiHome, 'egress', 'runtime')
}

export interface QuitMarker {
  reason: 'user-quit'
  at: number
}

/**
 * Leave a **timestamped** "user quit" note for the detached helpers.
 *
 * Timestamped and not merely present: a stale marker must never retire a helper started after it,
 * so every reader compares `at` against its own start time. Returns false when the write fails —
 * the registry hand-back has already happened by then, which is the part the machine needs.
 */
export function writeQuitMarker(runtimeDir: string, now: number = Date.now()): boolean {
  try {
    mkdirSync(runtimeDir, { recursive: true })
    const marker: QuitMarker = { reason: 'user-quit', at: now }
    writeFileSync(path.join(runtimeDir, QUIT_MARKER_NAME), JSON.stringify(marker), 'utf8')

    return true
  } catch {
    return false
  }
}

/**
 * The quit path's one call: give the machine its routing back **and** tell the permanent helpers
 * this exit was deliberate. Never throws — a quit handler that fails here leaves the machine on a
 * dead proxy, so both halves are best-effort by construction.
 */
export function releaseEgressOnQuit(log: (line: string) => void, runtimeDir: string): QuitProxyVerdict {
  const verdict = restoreLoopbackSystemProxy(log)

  if (writeQuitMarker(runtimeDir)) {
    log('[egress-proxy] quit: 已告知 egress 侧「本次是用户主动退出」——独立守护与看门狗下一个 tick 自行收尾')
  } else {
    log('[egress-proxy] quit: 退出标记没写成；系统代理已按上面的结论处理，但独立守护只能等它自己判定')
  }

  return verdict
}
