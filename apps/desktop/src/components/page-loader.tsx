import type { ComponentProps } from 'react'

import { TurtleLoader } from '@/components/ui/turtle-loader'
import { cn } from '@/lib/utils'

interface PageLoaderProps extends Omit<ComponentProps<'div'>, 'children'> {
  label?: string
}

export function PageLoader({
  'aria-label': ariaLabel,
  className,
  label = 'Loading',
  role = 'status',
  ...props
}: PageLoaderProps) {
  return (
    <div
      {...props}
      aria-label={ariaLabel ?? label}
      className={cn('grid h-full place-items-center', className)}
      role={role}
    >
      <TurtleLoader
        aria-hidden="true"
        className="text-primary/70"
        role="presentation"
        size="md"
      />
    </div>
  )
}
