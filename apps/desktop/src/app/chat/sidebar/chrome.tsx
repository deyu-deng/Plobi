import type * as React from 'react'

import { SidebarPanelLabel } from '@/app/shell/sidebar-label'
import { Codicon } from '@/components/ui/codicon'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { RowButton } from '@/components/ui/row-button'
import { cn } from '@/lib/utils'

// Shared, content-agnostic sidebar chrome — used by both the flat session
// sections and the project/workspace tree, so it lives outside either to keep
// imports one-directional (no index <-> projects cycle).

/** `loaded/total` when there's more on the server, else just the loaded count. */
export const countLabel = (loaded: number, total: number): string =>
  total > loaded ? `${loaded}/${total}` : String(loaded)

/** The muted count chip next to a section/workspace label. */
export function SidebarCount({ children }: { children: React.ReactNode }) {
  return <span className="text-[0.6875rem] font-medium text-(--ui-text-quaternary)">{children}</span>
}

/**
 * Every status dot in a sidebar row — session, project, agent — is this size.
 * It used to be `size-1` on one row family and `size-2` on another, so rows
 * whose label baselines matched still read as two different lists.
 */
export const SIDEBAR_ROW_DOT_SIZE = 'size-1.5' as const

/**
 * The collapsible header above a sidebar section. One component for every
 * section (recents, cron, messaging, projects, agent categories) so a fold
 * toggle can't differ by row family — it lived privately inside
 * `sessions-section.tsx` while the agent column grew its own copy.
 */
export interface SidebarSectionHeaderProps {
  action?: React.ReactNode
  collapsible?: boolean
  icon?: React.ReactNode
  label: string
  meta?: React.ReactNode
  onToggle: () => void
  open: boolean
}

export function SidebarSectionHeader({
  label,
  open,
  onToggle,
  action,
  meta,
  icon,
  collapsible = true
}: SidebarSectionHeaderProps) {
  const labelBody = (
    <>
      {icon}
      <SidebarPanelLabel>{label}</SidebarPanelLabel>
      {meta && <SidebarCount>{meta}</SidebarCount>}
    </>
  )

  return (
    <div className="group/section flex shrink-0 items-center justify-between gap-1 pb-1 pt-1.5">
      {collapsible ? (
        <button
          className="group/section-label flex w-fit items-center gap-1 bg-transparent text-left leading-none"
          onClick={onToggle}
          type="button"
        >
          {labelBody}
          <DisclosureCaret
            className="text-(--ui-text-tertiary) opacity-0 transition group-hover/section-label:opacity-100"
            open={open}
          />
        </button>
      ) : (
        <div className="flex w-fit items-center gap-1 leading-none">{labelBody}</div>
      )}
      {action}
    </div>
  )
}

// ── Row geometry (session row is canonical — everything composes these) ─────
//
// Height lives ONLY on SidebarRowShell (min-h-[1.625rem]). Inset children
// stretch to fill the cell and center content internally — never items-center
// on the shell grid, or short clusters (projects) float 1–2px off sessions.

const rowMinH = 'min-h-[1.625rem]'
const rowPadX = 'pl-2 pr-1'
const rowGap = 'gap-1.5'
const rowLead = 'grid size-3.5 shrink-0 place-items-center'
const rowInset = cn(rowPadX, rowGap, 'flex h-full min-w-0 items-center self-stretch py-0.5')
const rowLabel = 'min-w-0 truncate text-[0.8125rem] leading-none text-(--ui-text-secondary)'

/** Codicon size in sidebar row leads — matches the file tree (`tree.tsx`). */
export const SIDEBAR_LEAD_ICON_SIZE = '0.875rem' as const

/** Vertical stack of rows (gap-px, single column). */
export function SidebarRowStack({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('grid grid-cols-[minmax(0,1fr)] gap-px', className)} {...props} />
}

/** Nested rows (session previews, worktree bodies). */
export function SidebarRowNest({ className, ...props }: React.ComponentProps<'div'>) {
  return <SidebarRowStack className={cn('pb-1 pl-4', className)} {...props} />
}

/** Outer grid — sole owner of row height. */
export function SidebarRowShell({
  actions,
  children,
  className,
  ...props
}: React.ComponentProps<'div'> & { actions?: React.ReactNode }) {
  return (
    <div className={cn(rowMinH, 'grid grid-cols-[minmax(0,1fr)_auto] items-stretch rounded-md', className)} {...props}>
      {children}
      {actions ? <div className="flex shrink-0 items-center self-center">{actions}</div> : null}
    </div>
  )
}

/** Multi-control left cluster (project rows). */
export function SidebarRowCluster({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn(rowInset, className)} {...props} />
}

/** Session row main tap target. */
export function SidebarRowBody({ className, ...props }: React.ComponentProps<'button'>) {
  return <RowButton className={cn(rowInset, 'bg-transparent text-left', className)} {...props} />
}

/** Tappable label — underline/truncate live on the inner span, not the button. */
export function SidebarRowLink({
  className,
  labelClassName,
  children,
  ...props
}: React.ComponentProps<'button'> & { labelClassName?: string }) {
  return (
    <RowButton className={cn('min-w-0 shrink bg-transparent p-0 text-left', className)} {...props}>
      <span className={cn(rowLabel, labelClassName)}>{children}</span>
    </RowButton>
  )
}

/** Fixed leading column (dot, icon, drag handle). */
export function SidebarRowLead({ className, ...props }: React.ComponentProps<'span'>) {
  return <span className={cn(rowLead, className)} {...props} />
}

/** Standard row label typography. */
export function SidebarRowLabel({ className, ...props }: React.ComponentProps<'span'>) {
  return <span className={cn(rowLabel, className)} {...props} />
}

/** Dot ↔ grabber swap for dnd-kit reorder rows. */
export function SidebarRowGrab({
  ariaLabel,
  children,
  className,
  dragging = false,
  dragHandleProps,
  leadClassName
}: {
  ariaLabel: string
  children: React.ReactNode
  className?: string
  dragging?: boolean
  dragHandleProps?: React.HTMLAttributes<HTMLElement>
  leadClassName?: string
}) {
  return (
    <SidebarRowLead
      {...dragHandleProps}
      aria-label={ariaLabel}
      className={cn(
        'group/handle relative cursor-grab touch-none overflow-hidden active:cursor-grabbing',
        leadClassName,
        className
      )}
      data-reorder-handle
      onClick={event => event.stopPropagation()}
    >
      <span className="grid size-full place-items-center transition-opacity group-hover/handle:opacity-0 group-focus-within/handle:opacity-0">
        {children}
      </span>
      <Codicon
        className={cn(
          'absolute text-(--ui-text-quaternary) opacity-0 transition-opacity group-hover/handle:opacity-80 group-focus-within/handle:opacity-80 hover:text-(--ui-text-secondary)',
          dragging && 'text-(--ui-text-secondary) opacity-100'
        )}
        name="grabber"
        size="0.75rem"
      />
    </SidebarRowLead>
  )
}

/** Icon/dot slot inside SidebarRowLead — caps visual size so rows align. */
export function SidebarRowLeadGlyph({
  children,
  className,
  style
}: {
  children: React.ReactNode
  className?: string
  style?: React.CSSProperties
}) {
  return (
    <span
      className={cn('grid size-full place-items-center text-(--ui-text-tertiary) [&_.codicon]:leading-none', className)}
      style={style}
    >
      {children}
    </span>
  )
}
