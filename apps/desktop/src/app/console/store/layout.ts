import { Codecs, persistentAtom } from '@/lib/persisted'

/**
 * Console layout preferences.
 *
 * Collapse state is a user preference, not server data, so persistence lives
 * beside the atom that owns it (DESIGN.md, "State (TypeScript)").
 */

const LEFT_COLLAPSED_KEY = 'plobi.desktop.consoleLeftCollapsed'
const RIGHT_COLLAPSED_KEY = 'plobi.desktop.consoleRightCollapsed'

export const $consoleLeftCollapsed = persistentAtom(LEFT_COLLAPSED_KEY, false, Codecs.bool)

// Declared with U1 but first used by the right rail (U3), so the two rails
// remember their state the same way from the start.
export const $consoleRightCollapsed = persistentAtom(RIGHT_COLLAPSED_KEY, false, Codecs.bool)

export function toggleConsoleLeft(): void {
  $consoleLeftCollapsed.set(!$consoleLeftCollapsed.get())
}

export function toggleConsoleRight(): void {
  $consoleRightCollapsed.set(!$consoleRightCollapsed.get())
}
