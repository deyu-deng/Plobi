// A route out for the desktop's OWN git children — in git's dialect.
//
// Why this file exists (root fix for "We couldn't reach the update server"):
// the update check, every `git` probe the desktop runs, and `plobi update`
// all spawn `git` as a child process. `session.setProxy()` (egress-proxy.ts)
// only tells **Chromium** where to go; a child process gets a bare
// `{...process.env}` and therefore has to route itself. On a machine whose
// direct egress to github.com is intercepted (直连官方源被本机代理拦) and whose
// GUI environment carries no proxy variables, that child fails with
// `Failed to connect to github.com:443` / `schannel: server closed abruptly`
// and the About panel can only say "couldn't reach the update server".
//
// So: one reading, several renderings (裁定 94) — the egress table says how the
// desktop leaves the machine, and this module renders that answer as
// `http_proxy`-shaped environment variables for git. Nothing here decides
// routing; it only translates. Order of preference lives in main.ts's
// resolveGitProxyUrl():
//   1. explicit override  (PLOBI_GIT_PROXY / PLOBI_DESKTOP_GIT_PROXY)
//   2. already-all-there  (the launcher's own env wins; we never overwrite it)
//   3. the egress table    (desktopProxyConfig view → proxyRules)
//   4. the OS's answer     (session.resolveProxy() — covers system/WinINET)
//   5. direct              (no proxy env at all)
//
// Extracted from main.ts so the parsing is unit-testable without Electron.

const PROXY_ENV_KEYS = [
  'http_proxy',
  'https_proxy',
  'all_proxy',
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'ALL_PROXY'
]

// Does the caller's environment already carry a proxy? If it does we keep our
// hands off it: overwriting a launcher's own route with a different one is how
// you get "works in terminal, broken in the app" and its mirror image.
function hasProxyEnv(env) {
  return PROXY_ENV_KEYS.some(key => String(env?.[key] ?? '').trim())
}

// A proxy URL → the env vars git's HTTP transport reads (curl dialect). All
// three schemes are set because git uses http_proxy for http://, https_proxy
// for https://, and all_proxy for everything else — setting only one leaves
// `git ls-remote https://…` unproxied on some versions.
function proxyUrlToGitEnv(proxyUrl) {
  const url = String(proxyUrl || '')
    .trim()
    .replace(/\/+$/, '')

  if (!url) {
    return {}
  }

  const env = {}

  for (const key of PROXY_ENV_KEYS) {
    env[key] = url
  }

  return env
}

// Chromium's resolveProxy() answer → a git-usable proxy URL.
//   "DIRECT"                     → null  (leave git alone)
//   "PROXY 127.0.0.1:7890"       → "http://127.0.0.1:7890"
//   "PROXY a:1; PROXY b:2"       → first entry (Chromium's own preference)
//   "SOCKS5 127.0.0.1:1080"      → "socks5://127.0.0.1:1080"
//   "SOCKS 127.0.0.1:1080"       → "socks4://127.0.0.1:1080" (curl's name for SOCKS4)
// Anything unrecognised (QUIC, https-proxy forms, empty) → null.
function parseChromiumProxyResult(answer) {
  const text = String(answer || '').trim()

  if (!text || /^direct$/i.test(text)) {
    return null
  }

  for (const entry of text.split(';')) {
    const trimmed = entry.trim()

    if (!trimmed) {
      continue
    }

    const [scheme, target] = trimmed.split(/\s+/, 2)

    if (!target) {
      continue
    }

    if (/^proxy$/i.test(scheme)) {
      return normalizeProxyTarget(target)
    }

    if (/^socks5$/i.test(scheme)) {
      // Chromium says "SOCKS5 host:port"; curl wants the version in the scheme.
      return `socks5://${target}`
    }

    if (/^socks$/i.test(scheme)) {
      return `socks4://${target}`
    }
  }

  return null
}

// Chromium proxyRules dialect → one usable rule, or null.
//   "127.0.0.1:7890"                       → "http://127.0.0.1:7890"  (bare = all schemes)
//   "http=p:80;https=p:80"                 → first explicit one
//   "http://p:80"                          → as-is
//   "socks5://p:80"                        → as-is
//   "direct://" / "" / "*,direct://"       → null
function proxyFromProxyRules(rules) {
  const text = String(rules || '').trim()

  if (!text) {
    return null
  }

  for (const entry of text.split(/[;,]/)) {
    const rule = entry.trim()

    if (!rule || /^(?:\*|<[^>]*>)/.test(rule)) {
      continue
    }

    const eq = rule.indexOf('=')

    if (eq >= 0) {
      const scheme = rule.slice(0, eq).trim().toLowerCase()
      const target = rule.slice(eq + 1).trim()

      if (!target || !/^https?$/.test(scheme)) {
        continue // git's HTTP transport speaks http(s) proxies; skip the rest
      }

      return normalizeProxyTarget(target, scheme)
    }

    if (rule.startsWith('direct:')) {
      continue
    }

    return normalizeProxyTarget(rule)
  }

  return null
}

// Host[:port] → scheme://host:port. An explicit scheme is kept; a bare host
// defaults to http, which is what every local proxy here speaks.
function normalizeProxyTarget(target, scheme = 'http') {
  const value = String(target || '').trim()

  if (!value) {
    return null
  }

  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) {
    return value
  }

  return `${scheme}://${value}`
}

export {
  hasProxyEnv,
  parseChromiumProxyResult,
  PROXY_ENV_KEYS,
  proxyFromProxyRules,
  proxyUrlToGitEnv
}
