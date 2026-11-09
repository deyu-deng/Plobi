/**
 * One row's "not right now" state, read the same way in every picker.
 *
 * The backend marks a model that is only *waiting* — a leg whose bucket ran out and
 * refills on its own — with `capabilities[model].unavailable`. A leg the user can
 * fix (a dead credential) is not offered at all; the reason stays a machine code and
 * the screens translate it (机器词不上屏).
 *
 * User, 2026-11-08 21:3x: 「能修的摘掉，等恢复的标灰并写剩余时间」.
 */

export interface ModelUnavailable {
  /** Machine code from the hub (`quota_exhausted`, …) — never shown raw. */
  reason: string
  /** Seconds left before the leg can serve again, when the hub said so. */
  retry_after_seconds?: number | null
  /** When it comes back, as the hub wrote it down. */
  available_at?: string | null
}

/** The row's own "waiting" state, or null when it may be picked. */
export function unavailableOf(capabilities: unknown): ModelUnavailable | null {
  const caps = capabilities as { unavailable?: Partial<ModelUnavailable> } | null | undefined
  const raw = caps?.unavailable

  if (!raw || typeof raw !== 'object') {
    return null
  }

  const reason = typeof raw.reason === 'string' ? raw.reason : ''

  if (!reason) {
    return null
  }

  return {
    reason,
    retry_after_seconds:
      typeof raw.retry_after_seconds === 'number' ? raw.retry_after_seconds : null,
    available_at: typeof raw.available_at === 'string' ? raw.available_at : null
  }
}

/**
 * "还有 56 分钟" — the machine seconds turned into one of the time phrases the app
 * already has in all four languages (no new copy, no raw timestamps on screen).
 */
export function remainingPhrase(
  seconds: number | null | undefined,
  words: { minutes: string; hours: string; days: string }
): string | null {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) {
    return null
  }

  const minutes = Math.ceil(seconds / 60)

  if (minutes < 60) {
    return words.minutes.replace('{n}', String(minutes))
  }

  const hours = Math.ceil(minutes / 60)

  if (hours < 24) {
    return words.hours.replace('{n}', String(hours))
  }

  return words.days.replace('{n}', String(Math.ceil(hours / 24)))
}
