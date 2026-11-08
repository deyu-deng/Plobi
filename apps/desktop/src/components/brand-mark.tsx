import { cn } from '@/lib/utils'

import { PLOBI_MARK_PATH, PLOBI_MARK_VIEW_BOX, PLOBI_WORDMARK } from './plobi-glyphs'

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

// Brand wordmark: the geometric "Plobi" — single-line letters, the turtle
// dotting the "i". Drawn inline so it takes the live theme color
// (`currentColor` inherits `--theme-primary`): switching skins repaints it.
//
// One renderer for every call site (the chat intro and the console empty
// state): two copies of this SVG would drift, and the drift would show up as
// "the wordmark looks different in this one screen".
export function PlobiWordmark({ className, ...props }: React.ComponentProps<'svg'>) {
  return (
    <svg
      aria-label="Plobi"
      className={cn('h-auto w-full text-(--theme-primary)', className)}
      role="img"
      viewBox={PLOBI_WORDMARK.viewBox}
      {...props}
    >
      <g
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth={PLOBI_WORDMARK.strokeWidth}
        transform={PLOBI_WORDMARK.groupTransform}
      >
        {PLOBI_WORDMARK.shapes.map((shape, index) =>
          shape.kind === 'line' ? (
            <line key={index} x1={shape.x1} x2={shape.x2} y1={shape.y1} y2={shape.y2} />
          ) : (
            <circle cx={shape.cx} cy={shape.cy} key={index} r={shape.r} />
          )
        )}
        <g transform={PLOBI_WORDMARK.tittle.transform}>
          <path d={PLOBI_WORDMARK.tittle.path} fill="currentColor" fillRule="nonzero" stroke="none" />
        </g>
      </g>
    </svg>
  )
}
