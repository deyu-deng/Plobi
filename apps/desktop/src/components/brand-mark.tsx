import { cn } from '@/lib/utils'

import { PLOBI_MARK_PATH, PLOBI_MARK_VIEW_BOX } from './plobi-glyphs'

// Brand glyph: the Plobi turtle (dome shell + head + four skirt bumps), drawn
// inline so it can take the live theme color — `currentColor` inherits
// `--theme-primary`, so the glyph changes with the skin the user picks.
// Shape never changes, only color: you recognise the turtle, not the hue.
//
// The tile is the neutral elevated surface, not a hardcoded white. The white
// tile was a literal sanctioned for the old avatar glyph and read as a white
// brick in dark mode; the mark needs *a* backdrop, not *that* backdrop.
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span
      className={cn(
        'inline-flex size-14 shrink-0 items-center justify-center overflow-hidden rounded-md bg-(--ui-bg-elevated) text-(--theme-primary)',
        className
      )}
      {...props}
    >
      <svg aria-hidden="true" className="size-full" fill="currentColor" focusable="false" viewBox={PLOBI_MARK_VIEW_BOX}>
        <path d={PLOBI_MARK_PATH} fillRule="nonzero" />
      </svg>
    </span>
  )
}
