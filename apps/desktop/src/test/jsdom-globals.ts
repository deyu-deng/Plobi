/**
 * Fill the browser globals that jsdom (or this Node version) does not provide,
 * so a red test means a real regression rather than a missing global.
 *
 * `CSS.escape` — used by the message timeline to find a row by id — is not
 * implemented by jsdom; without it the whole thread renderer dies with
 * "Cannot read properties of undefined (reading 'escape'".
 *
 * `localStorage` / `sessionStorage` — Node >= 22.4 defines an experimental
 * `globalThis.localStorage` that is `undefined` unless the process was started
 * with `--localstorage-file` (it prints "localStorage is not available…" on
 * every run), and that undefined value reaches the jsdom window too: with the
 * storage install below commented out, `src/lib/storage.test.ts` and
 * `src/store/preview.test.ts` die with "Cannot read properties of undefined
 * (reading 'clear')" even though `environment: 'jsdom'` is set in the config.
 *
 * The one-line fix would be `NODE_OPTIONS=--no-experimental-webstorage`, but
 * that flag does not exist on older Node, so it would break the very machines
 * we might build on again. Filling the gap here keeps the test environment
 * identical across Node versions and needs no shell setup to remember.
 */

class MemoryStorage implements Storage {
  private map = new Map<string, string>()

  get length() {
    return this.map.size
  }

  clear(): void {
    this.map.clear()
  }

  getItem(key: string): string | null {
    return this.map.has(key) ? (this.map.get(key) as string) : null
  }

  key(index: number): string | null {
    return Array.from(this.map.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.map.delete(key)
  }

  setItem(key: string, value: string): void {
    this.map.set(key, String(value))
  }
}

function usable(storage: unknown): boolean {
  return (
    !!storage &&
    typeof (storage as Storage).setItem === 'function' &&
    typeof (storage as Storage).getItem === 'function'
  )
}

function install(name: 'localStorage' | 'sessionStorage'): void {
  if (usable((globalThis as Record<string, unknown>)[name])) return

  const value = new MemoryStorage()
  for (const target of [globalThis, typeof window === 'undefined' ? null : window]) {
    if (!target) continue
    try {
      Object.defineProperty(target, name, {
        value,
        writable: true,
        configurable: true,
        enumerable: true,
      })
    } catch {
      // A non-configurable host binding means the runtime already provides a
      // working one; nothing to do.
    }
  }
}

install('localStorage')
install('sessionStorage')

// jsdom has no `CSS`; every real browser does. We only use `escape`.
if (typeof (globalThis as Record<string, unknown>).CSS === 'undefined') {
  Object.defineProperty(globalThis, 'CSS', {
    value: {
      escape: (value: string) => String(value).replace(/[^\w-]/g, ch => `\\${ch}`),
      supports: () => false,
    },
    writable: true,
    configurable: true,
  })
}
