/**
 * Display-layer scrubber for injected internal directives (WP-UI-NO-INTERNALS).
 *
 * The §8.2 hard route (`plugins/plobi-north-star/hard_route.py`, WP-BE-13)
 * rewrites the stored user turn into:
 *
 *     <用户原话>\n\n[§8.2 硬路由 · 本回合强制] 上面这句话命中总秘书冻结话术…
 *
 * That rewrite is for the model, not for the human: the bubble, the up-arrow
 * input history, the edit composer and the session titles must keep showing
 * only what the user actually typed. The stored row is never rewritten — this
 * is a read-only trim applied where text becomes visible.
 */

/** Marker prefix of the injected route directive. Kept narrow on purpose. */
const HARD_ROUTE_MARKER = '[§8.2 硬路由'

/**
 * Cut everything from the injected directive onward.
 *
 * Text without the marker comes back untouched (trailing whitespace aside), so
 * this doubles as the pass-through for every other message.
 */
export function stripInternalDirectives(text: string): string {
  const value = text ?? ''
  const index = value.indexOf(HARD_ROUTE_MARKER)

  return (index === -1 ? value : value.slice(0, index)).trimEnd()
}
