import { atom } from 'nanostores'

import { updateAtom, type Updater } from '@/lib/store-utils'

// Stored session ids currently marked "working" (a turn is streaming / a tool
// is running). Lives here so the watchdog module can share it without a cycle —
// store/session.ts re-exports it for existing importers, and
// store/session-watchdog.ts imports it directly.
export const $workingSessionIds = atom<string[]>([])
export const setWorkingSessionIds = (next: Updater<string[]>) => updateAtom($workingSessionIds, next)
