import type { ReactNode } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

export type RightRailTab = 'agenda' | 'files'

export interface RightRailWorkbenchLabels {
  aria: string
  tabs: {
    agenda: string
    files: string
  }
  noFolderTitle: string
  noFolderBody: string
}

const TABS = [
  { icon: 'folder-opened', id: 'files' },
  { icon: 'calendar', id: 'agenda' }
] as const

export function RightRailWorkbench({
  activeTab,
  agenda,
  files,
  labels,
  onTabChange,
  trailing
}: {
  activeTab: RightRailTab
  agenda: ReactNode
  files: ReactNode | null
  labels: RightRailWorkbenchLabels
  onTabChange: (tab: RightRailTab) => void
  trailing?: ReactNode
}) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div
        aria-label={labels.aria}
        className="flex h-8 shrink-0 items-center gap-1 border-b border-(--ui-stroke-tertiary) px-1"
        role="tablist"
      >
        <div className="flex min-w-0 flex-1 items-center">
          {TABS.map(tab => (
            <button
              aria-selected={activeTab === tab.id}
              className={cn(
                'flex h-7 min-w-0 items-center gap-1 rounded-sm border-b-2 px-2 text-[0.68rem] transition-colors',
                activeTab === tab.id
                  ? 'border-(--ui-accent) text-foreground'
                  : 'border-transparent text-(--ui-text-tertiary) hover:text-foreground'
              )}
              data-testid={`right-rail-tab-${tab.id}`}
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              role="tab"
              type="button"
            >
              <Codicon name={tab.icon} size="0.85rem" />
              <span className="truncate">{labels.tabs[tab.id]}</span>
            </button>
          ))}
        </div>
        {trailing}
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div
          aria-label={labels.tabs.files}
          className="flex min-h-0 flex-1 flex-col"
          data-testid="right-rail-panel-files"
          hidden={activeTab !== 'files'}
          role="tabpanel"
        >
          {files ?? (
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
              <Codicon name="folder" size="1.25rem" />
              <p className="text-[0.78rem] font-medium text-foreground">{labels.noFolderTitle}</p>
              <p className="text-[0.68rem] text-(--ui-text-tertiary)">{labels.noFolderBody}</p>
            </div>
          )}
        </div>
        <div
          aria-label={labels.tabs.agenda}
          className="flex min-h-0 flex-1 flex-col"
          data-testid="right-rail-panel-agenda"
          hidden={activeTab !== 'agenda'}
          role="tabpanel"
        >
          {agenda}
        </div>
      </div>
    </div>
  )
}
