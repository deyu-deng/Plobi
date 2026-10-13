// R-083 — the retry card for a broken module graph.
//
// Everything the renderer loads lives in one module graph, and until now every
// module of it was statically imported by `src/main.tsx`: a break anywhere (a
// dev HMR shard that half-applied mid-session, an edit landing between module
// evaluations) threw BEFORE `createRoot(...).render(...)` ever ran. The
// ErrorBoundary inside the React tree cannot catch a bootstrap failure, and the
// user was left with a white window and nothing to click (92× "Failed to
// reload" in desktop.log, 2026-10-13 — see Docs/tasks/ux-hygiene-task.md).
//
// This card is deliberately dependency-free — plain DOM, no React, no i18n
// runtime, no stores — because every one of those is itself inside the module
// graph that just broke. Its copy mirrors `boot.recovery.*` in src/i18n/ (the
// normal-render-path registration of the same strings; asserted in lockstep by
// ./boot-recovery.test.ts) and is picked by `navigator.language` here so the
// card still speaks Chinese on a zh machine even with the i18n runtime gone.
//
// It renders into `#root` — the mount point the app itself uses — and reloads
// the window on click. A reload is the only recovery that exists: it re-fetches
// the module graph from the dev server (or the built bundle), and the retry
// card removes itself the moment the app mounts over it.

const CARD_CLASS = 'plobi-boot-recovery'
const CARD_ID = 'plobi-boot-recovery'

export interface BootRecoveryCopy {
  description: string
  details: string
  retry: string
  title: string
}

/** Mirrors `boot.recovery.*` in src/i18n/{en,zh}.ts — keep the two in lockstep. */
export const BOOT_RECOVERY_COPY: Record<'en' | 'zh', BootRecoveryCopy> = {
  en: {
    description:
      'Plobi failed to load and the window cannot start. Retry reloads the window — your chats and settings are untouched.',
    details: 'Error details',
    retry: 'Retry',
    title: 'Load failed'
  },
  zh: {
    description: 'Plobi 加载失败，窗口无法启动。点击重试会重新加载窗口——你的对话与设置不受影响。',
    details: '错误详情',
    retry: '点击重试',
    title: '加载失败'
  }
}

// System color keywords (Canvas / CanvasText / GrayText / ButtonFace …) adapt to
// light/dark on their own once color-scheme is set — no media queries, no
// Tailwind, nothing outside this file.
const STYLES = `
.${CARD_CLASS}-wrap {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  color-scheme: light dark;
  background: Canvas;
  color: CanvasText;
}
.${CARD_CLASS} {
  width: min(26rem, 100%);
  border: 1px solid GrayText;
  border-radius: 0.75rem;
  padding: 1.25rem;
}
.${CARD_CLASS}-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
}
.${CARD_CLASS}-description {
  margin: 0.5rem 0 1rem;
  font-size: 0.8125rem;
  line-height: 1.5;
  opacity: 0.85;
}
.${CARD_CLASS}-retry {
  border: 1px solid ButtonText;
  border-radius: 0.5rem;
  background: ButtonFace;
  color: ButtonText;
  cursor: pointer;
  font-size: 0.8125rem;
  font-weight: 600;
  padding: 0.375rem 0.875rem;
}
.${CARD_CLASS}-retry:hover {
  filter: brightness(1.1);
}
.${CARD_CLASS}-details {
  margin-top: 1rem;
  font-size: 0.75rem;
  opacity: 0.8;
}
.${CARD_CLASS}-details pre {
  max-height: 8rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0.5rem 0 0;
  font-size: 0.6875rem;
  line-height: 1.5;
}
`

function pickCopy(): BootRecoveryCopy {
  const language = (typeof navigator === 'undefined' ? '' : navigator.language || '').toLowerCase()

  return language.startsWith('zh') ? BOOT_RECOVERY_COPY.zh : BOOT_RECOVERY_COPY.en
}

function errorText(error: unknown): string {
  if (error instanceof Error) {
    return error.stack || `${error.name}: ${error.message}`
  }

  return typeof error === 'string' ? error : JSON.stringify(error) ?? String(error)
}

/**
 * Paint the retry card. Idempotent — a second call while the card is on screen
 * (a second broadcast racing the first) does not stack another one.
 *
 * `reloadWindow` is injectable so the test can assert the retry click without
 * navigating jsdom; production passes `window.location.reload`.
 */
export function renderBootRecoveryCard(error?: unknown, reloadWindow: () => void = () => window.location.reload()): void {
  if (typeof document === 'undefined' || document.getElementById(CARD_ID)) {
    return
  }

  const copy = pickCopy()
  const style = document.createElement('style')
  style.textContent = STYLES

  const wrap = document.createElement('div')
  wrap.id = CARD_ID
  wrap.className = `${CARD_CLASS}-wrap`
  wrap.setAttribute('role', 'alert')

  const card = document.createElement('div')
  card.className = CARD_CLASS

  const title = document.createElement('h1')
  title.className = `${CARD_CLASS}-title`
  title.textContent = copy.title

  const description = document.createElement('p')
  description.className = `${CARD_CLASS}-description`
  description.textContent = copy.description

  const retry = document.createElement('button')
  retry.className = `${CARD_CLASS}-retry`
  retry.type = 'button'
  retry.textContent = copy.retry
  retry.onclick = () => reloadWindow()

  card.append(title, description, retry)

  const text = errorText(error)

  if (text) {
    const details = document.createElement('details')
    details.className = `${CARD_CLASS}-details`

    const summary = document.createElement('summary')
    summary.textContent = copy.details

    const pre = document.createElement('pre')
    pre.textContent = text

    details.append(summary, pre)
    card.append(details)
  }

  wrap.append(card)
  ;(document.getElementById('root') ?? document.body).append(style, wrap)
}

export interface BootstrapDeps {
  /** Which branch of the entry point this window is: the app or the pet overlay. */
  isOverlayWindow: boolean
  /** Mounts the full application (dynamic import of ./bootstrap-app). */
  loadApp: () => unknown
  /** Mounts the pet overlay surface (dynamic import of ./app/pet-overlay/overlay-root). */
  loadOverlay: () => unknown
  /** Injected for tests; defaults to reloading the window. */
  reloadWindow?: () => void
}

/**
 * The R-083 entry point. Loads exactly one branch — the app, or the pet overlay
 * for `?win=overlay` windows — and turns ANY failure of that load into the
 * retry card. The two branches stay separate: an overlay window is never routed
 * through the app mount, and the app branch is never routed through the
 * overlay.
 */
export function runBootstrap(deps: BootstrapDeps): void {
  const load = deps.isOverlayWindow ? deps.loadOverlay : deps.loadApp

  void Promise.resolve()
    .then(load)
    .catch(error => renderBootRecoveryCard(error, deps.reloadWindow))
}
