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
