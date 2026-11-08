import { type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'

/**
 * Resize-sash look, reverse-engineered from Qoder's packaged renderer
 * (`D:\Software\Qoder\resources\app.asar` → `out/renderer/assets/index-*.js`):
 *
 *  - The divider is always a **1px line** (`w-px` / `h-px` on `bg-border-tertiary`).
 *    Hovering never grows a wide filled band.
 *  - Hover paints a **gradient overlay** on top of that line: transparent at the
 *    ends, the brand color at the center, with the center following the pointer
 *    (`linear-gradient(to bottom, transparent …, primary-border …, primary 50%, …)`
 *    plus a `--…-center` var tracking `clientY`).
 *  - The grab strip stays wider than the line — but transparent.
 *
 * Both sashes in this app (the column sash in `pane-shell` and the horizontal
 * one in `master-detail`) share this one shape, so they can't drift apart.
 */

/** `x` = vertical bar (columns), `y` = horizontal bar (rows). */
export type SashAxis = 'x' | 'y'

/** Custom property that carries the glow's center along the sash. */
export const SASH_GLOW_CENTER_VAR = '--sash-glow-center'

// Shoulder = faint accent (Qoder's `primary-border`), core = the brand color
// (Qoder's `primary`). Both come from the app's own tokens.
const GLOW_SHOULDER = 'var(--ui-sash-hover-border)'
const GLOW_CORE = 'var(--ui-accent)'
// Qoder's numbers: the core is 56px wide, the whole glow fades out over 120px.
const GLOW_CORE_PX = 56
const GLOW_FALLOFF_PX = 120

/**
 * The gradient overlay painted on top of the 1px divider while hovering or
 * dragging. Transparent at both ends, so only the segment around the pointer
 * is visible — a soft band of brand color instead of a solid bar.
 */
export function sashGlowStyle(axis: SashAxis): CSSProperties {
  const direction = axis === 'x' ? 'to bottom' : 'to right'
  const center = `var(${SASH_GLOW_CENTER_VAR}, 50%)`

  return {
    backgroundImage: [
      `linear-gradient(${direction},`,
      'transparent 0%,',
      `transparent calc(${center} - ${GLOW_FALLOFF_PX}px),`,
      `${GLOW_SHOULDER} calc(${center} - ${GLOW_CORE_PX}px),`,
      `${GLOW_CORE} ${center},`,
      `${GLOW_SHOULDER} calc(${center} + ${GLOW_CORE_PX}px),`,
      `transparent calc(${center} + ${GLOW_FALLOFF_PX}px),`,
      'transparent 100%)'
    ].join(' ')
  }
}

/**
 * Where the glow should peak, in px from the sash's start edge. Follows the
 * pointer (Qoder tracks `clientY` on its workspace handle) and is clamped to
 * the sash's own length.
 */
export function sashGlowCenter(event: ReactPointerEvent<HTMLElement>, axis: SashAxis): number {
  const rect = event.currentTarget.getBoundingClientRect()
  const size = axis === 'x' ? rect.height : rect.width
  const raw = axis === 'x' ? event.clientY - rect.top : event.clientX - rect.left

  return Math.max(0, Math.min(size, raw))
}
