import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * The status vocabulary of a provider / subscription row, in one place.
 *
 * Two shapes only, because the card system allows two (`Docs/specs/settings-card-
 * system.md` §2): the **dot** answers "what state is this in" and the **badge**
 * answers "why". Both are used by the subscription cards; the keys screen can adopt
 * them without inventing a third visual language.
 *
 * Tones map onto three meanings and nothing else:
 * - `live`   — set up and serving (primary);
 * - `idle`   — not set up yet, or switched off by the user. **Not** an error;
 * - `error`  — set up but not usable right now (red), the one case that needs a why.
 */
export type ProviderStatusTone = 'error' | 'idle' | 'live'

const DOT_CLASS: Record<ProviderStatusTone, string> = {
  error: 'bg-destructive',
  idle: 'bg-(--ui-stroke-secondary)',
  live: 'bg-primary'
}

const BADGE_CLASS: Record<ProviderStatusTone, string> = {
  error: 'border-destructive/30 bg-destructive/10 text-destructive',
  idle: 'border-(--ui-stroke-secondary) bg-transparent text-muted-foreground',
  live: 'border-primary/30 bg-primary/10 text-primary'
}

/** The 8px dot next to a row's name. Purely presentational; `aria-hidden` by default. */
export function ProviderStatusDot({
  className,
  label,
  tone
}: {
  className?: string
  /** When given, the dot becomes the accessible name of the state (otherwise silent). */
  label?: string
  tone: ProviderStatusTone
}) {
  if (label) {
    return (
      <span
        aria-label={label}
        className={cn('size-2 shrink-0 rounded-full', DOT_CLASS[tone], className)}
        role="img"
      />
    )
  }

  return <span aria-hidden className={cn('size-2 shrink-0 rounded-full', DOT_CLASS[tone], className)} />
}

/**
 * The reason a row is red, said in words next to the dot.
 *
 * Deliberately a `<span>`, not a button: the badge explains, it never acts, so it
 * must not look pressable (the acting control on the row is still the switch / `+`).
 */
export function ProviderStatusBadge({
  children,
  className,
  tone = 'error'
}: {
  children: ReactNode
  className?: string
  tone?: ProviderStatusTone
}) {
  return (
    <span
      className={cn(
        'inline-flex max-w-full shrink-0 items-center gap-1 rounded-full border px-1.5 py-px',
        'text-[length:var(--conversation-caption-font-size)] leading-none whitespace-nowrap',
        BADGE_CLASS[tone],
        className
      )}
    >
      {children}
    </span>
  )
}
