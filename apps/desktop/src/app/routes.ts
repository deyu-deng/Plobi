export const SESSION_ROUTE_PREFIX = '/'
/**
 * S1 L1 console — the cold-start landing route (spec §3.5 "落地路由反转").
 * The pre-U6 chat home is no longer `/`; it moved to the L2 workbench
 * (`/agent/:id`) and its fresh-draft form to `NEW_CHAT_ROUTE`.
 */
export const HOME_ROUTE = '/'
/** Fresh-draft chat route. Reachable by starting a new session, not from nav. */
export const NEW_CHAT_ROUTE = '/new'
export const AGENDA_ROUTE = '/agenda'
export const SETTINGS_ROUTE = '/settings'
export const COMMAND_CENTER_ROUTE = '/command-center'
export const SKILLS_ROUTE = '/skills'
export const MESSAGING_ROUTE = '/messaging'
export const ARTIFACTS_ROUTE = '/artifacts'
export const CRON_ROUTE = '/cron'
export const PROFILES_ROUTE = '/profiles'
export const AGENTS_ROUTE = '/agents'
export const CONSOLE_ROUTE = '/console'
export const STARMAP_ROUTE = '/starmap'

export type AppView =
  | 'agenda'
  | 'agents'
  | 'agent'
  | 'artifacts'
  | 'chat'
  | 'command-center'
  | 'console'
  | 'cron'
  | 'messaging'
  | 'profiles'
  | 'settings'
  | 'skills'
  | 'starmap'

export type AppRouteId =
  | 'agenda'
  | 'agents'
  | 'agent'
  | 'artifacts'
  | 'command-center'
  | 'console'
  | 'cron'
  | 'messaging'
  | 'new'
  | 'profiles'
  | 'settings'
  | 'skills'
  | 'starmap'

export interface AppRoute {
  id: AppRouteId
  path: string
  view: AppView
}

export const APP_ROUTES = [
  { id: 'new', path: NEW_CHAT_ROUTE, view: 'chat' },
  { id: 'settings', path: SETTINGS_ROUTE, view: 'settings' },
  { id: 'command-center', path: COMMAND_CENTER_ROUTE, view: 'command-center' },
  { id: 'skills', path: SKILLS_ROUTE, view: 'skills' },
  { id: 'messaging', path: MESSAGING_ROUTE, view: 'messaging' },
  { id: 'artifacts', path: ARTIFACTS_ROUTE, view: 'artifacts' },
  { id: 'cron', path: CRON_ROUTE, view: 'cron' },
  { id: 'agenda', path: AGENDA_ROUTE, view: 'agenda' },
  { id: 'profiles', path: PROFILES_ROUTE, view: 'profiles' },
  { id: 'agents', path: AGENTS_ROUTE, view: 'agents' },
  { id: 'console', path: CONSOLE_ROUTE, view: 'console' },
  { id: 'starmap', path: STARMAP_ROUTE, view: 'starmap' }
] as const satisfies readonly AppRoute[]

const APP_VIEW_BY_PATH = new Map<string, AppView>(APP_ROUTES.map(route => [route.path, route.view]))
const RESERVED_PATHS: ReadonlySet<string> = new Set(APP_ROUTES.map(route => route.path))

// Views that render as a full-screen modal card (OverlayView) over the shell.
// While one is open the app's titlebar control clusters must hide so they don't
// bleed over the overlay (they sit at a higher z-index than the overlay card).
//
// `console` and `agent` are deliberately NOT overlays any more (U6, spec §3.5):
// the three-column console is the cold-start landing and the L2 workbench is a
// first-class route, both rendered in the shell's main pane like the old chat
// home was.
export const OVERLAY_VIEWS: ReadonlySet<AppView> = new Set([
  'agenda',
  'agents',
  'command-center',
  'cron',
  'profiles',
  'settings',
  'starmap'
])

export function isOverlayView(view: AppView): boolean {
  return OVERLAY_VIEWS.has(view)
}

// Main-pane pages that replace the center column but keep the session sidebar.
// Plobi always left the left rail mounted on these routes; U6/WP-A gated the
// sidebar on `chatOpen` / secretary `level`, which hid it on /skills and made
// the page feel inescapable. Overlays (settings, cron, …) still cover the shell.
export const SHELL_PAGE_VIEWS: ReadonlySet<AppView> = new Set(['artifacts', 'messaging', 'skills'])

export function isShellPageView(view: AppView): boolean {
  return SHELL_PAGE_VIEWS.has(view)
}

export function isNewChatRoute(pathname: string): boolean {
  return pathname === NEW_CHAT_ROUTE
}

// S2 L2 workbench lives at `/agent/:id` (spec §6). `routes.ts` has only flat
// exact-match routes, so the parameterised agent route is matched separately
// here rather than added to `APP_ROUTES`.
const AGENT_ROUTE_PREFIX = '/agent/'

/** True for exactly `/agent/<id>` with a non-empty, slash-free id. */
export function isAgentRoute(pathname: string): boolean {
  if (!pathname.startsWith(AGENT_ROUTE_PREFIX)) {
    return false
  }

  const id = pathname.slice(AGENT_ROUTE_PREFIX.length)

  return id.length > 0 && !id.includes('/')
}

/** The agent id from `/agent/:id`, or null when the path isn't an agent route. */
export function agentRouteId(pathname: string): null | string {
  return isAgentRoute(pathname) ? decodeURIComponent(pathname.slice(AGENT_ROUTE_PREFIX.length)) : null
}

/** Build the S2 route for an agent id (used by the S1 left rail navigation). */
export function agentRoute(id: string): string {
  return `${AGENT_ROUTE_PREFIX}${encodeURIComponent(id)}`
}

/**
 * The secretary identity of a route (ARCH-UI-MASTER §4.3 / TASK WP-A):
 *   `'l1'`        → the master secretary (landing `/`)
 *   `{ l2: id }`  → a specialist L2 secretary (`/agent/:id`)
 *   `null`        → the original session chat (no secretary identity)
 *
 * The shell and center column never branch on this — only the left/right rail
 * slot CONTENT does. It is a pure function of the pathname, so no context is
 * needed: every consumer resolves it from `useLocation()` itself.
 */
export type ShellLevel = 'l1' | { l2: string }

export function shellLevelForPath(pathname: string): ShellLevel | null {
  const agentId = agentRouteId(pathname)

  if (agentId) {
    return { l2: agentId }
  }

  if (pathname === HOME_ROUTE || pathname === CONSOLE_ROUTE) {
    return 'l1'
  }

  return null
}

/**
 * 裁定 40 (WP-L1-CHROME): the git branch/worktree strip above the composer
 * (`CodingStatusRow`) is a **workshop** affordance — 「New branch」, 「34 40 6038
 * changed」. The chief secretary is not a labourer, so L1 never shows it at its
 * mouth. Project L2 workbenches keep it (`/agent/:id`), and so does the
 * original session chat (`level === null`).
 *
 * Pure predicate so the rule is testable without rendering the composer.
 */
export function showsComposerCodingRow(level: null | ShellLevel): boolean {
  return level !== 'l1'
}

/**
 * Desktop `routeToken` is `${pathname}:${search}:${hash}`. Pathnames in this app
 * never contain `:`, so the first segment is safe.
 */
export function pathnameFromRouteToken(routeToken: string): string {
  const cut = routeToken.indexOf(':')

  return cut === -1 ? routeToken : routeToken.slice(0, cut)
}

/**
 * Whether creating/opening a session should rewrite the URL to `/:sessionId`.
 * L1 (`/`) and L2 (`/agent/:id`) shells must stay put — WP-E removed pure-chat
 * hosting; navigating off them nulls `shellLevel` and bounces to home.
 */
export function shouldSyncSessionUrl(pathname: string): boolean {
  return shellLevelForPath(pathname) == null
}

export function routeSessionId(pathname: string): string | null {
  if (!pathname.startsWith(SESSION_ROUTE_PREFIX) || RESERVED_PATHS.has(pathname)) {
    return null
  }

  const id = pathname.slice(SESSION_ROUTE_PREFIX.length)

  return id && !id.includes('/') ? decodeURIComponent(id) : null
}

export function sessionRoute(sessionId: string): string {
  return `${SESSION_ROUTE_PREFIX}${encodeURIComponent(sessionId)}`
}

export function appViewForPath(pathname: string): AppView {
  if (isAgentRoute(pathname)) {
    return 'agent'
  }

  // WP-E: `/new` and `/:sessionId` no longer host a chat shell — the catch-all
  // bounces them to L1. Still parse `routeSessionId` for deep links, but do
  // NOT report `chat` or rails gated on `chatOpen` briefly enable against an
  // empty center (full-bleed leftover file-preview flash).
  if (isNewChatRoute(pathname) || routeSessionId(pathname)) {
    return 'console'
  }

  if (pathname === HOME_ROUTE) {
    return 'console'
  }

  // Unknown paths fall through to the console: it is the landing surface now,
  // and the router's catch-all redirects there too.
  return APP_VIEW_BY_PATH.get(pathname) ?? 'console'
}
