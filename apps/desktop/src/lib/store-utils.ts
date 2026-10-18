/** Shared helpers for nanostores atoms — extracted from store/session.ts so
 *  the split store modules don't re-declare them. */

export type Updater<T> = T | ((current: T) => T)

export interface AppAtom<T> {
  get: () => T
  set: (value: T) => void
}

export function updateAtom<T>(store: AppAtom<T>, next: Updater<T>) {
  store.set(typeof next === 'function' ? (next as (current: T) => T)(store.get()) : next)
}

// Toggle an id's membership in a string-set atom, no-op when unchanged (keeps
// the same array reference so subscribers don't churn).
export const toggleMembership = (set: (next: Updater<string[]>) => void, id: string, on: boolean) =>
  set(current => {
    const present = current.includes(id)

    if (on) {
      return present ? current : [...current, id]
    }

    return present ? current.filter(x => x !== id) : current
  })
