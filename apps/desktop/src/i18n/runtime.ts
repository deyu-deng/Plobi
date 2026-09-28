import { TRANSLATIONS } from './catalog'
import { DEFAULT_LOCALE } from './languages'
import type { Locale, Translations } from './types'

let runtimeLocale: Locale = DEFAULT_LOCALE

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function resolvePath(catalog: Translations, key: string): unknown {
  return key.split('.').reduce<unknown>((current, part) => {
    if (!isRecord(current)) {
      return undefined
    }

    return current[part]
  }, catalog)
}

function renderTranslation(value: unknown, args: unknown[]): string | null {
  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'function') {
    return (value as (...args: unknown[]) => string)(...args)
  }

  return null
}

export function setRuntimeI18nLocale(locale: Locale) {
  runtimeLocale = locale
}

/**
 * The locale `translateNow` is currently resolving against. Needed by non-hook
 * modules (e.g. src/lib/desktop-deferred-sidecars.ts) that render a string
 * supplied by the Electron main process in *two* languages and must pick one
 * outside of React.
 */
export function currentI18nLocale(): Locale {
  return runtimeLocale
}

export function translateNow(key: string, ...args: unknown[]): string {
  const active = renderTranslation(resolvePath(TRANSLATIONS[runtimeLocale], key), args)

  if (active !== null) {
    return active
  }

  if (runtimeLocale !== DEFAULT_LOCALE) {
    const fallback = renderTranslation(resolvePath(TRANSLATIONS[DEFAULT_LOCALE], key), args)

    if (fallback !== null) {
      return fallback
    }
  }

  return key
}
