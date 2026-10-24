import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  RightRailWorkbench,
  type RightRailTab,
  type RightRailTabDef,
  type RightRailWorkbenchLabels
} from './right-rail'

const labels: RightRailWorkbenchLabels = {
  add: 'Open tab',
  aria: 'Right rail',
  close: label => `Close ${label}`,
  emptyBody: 'Use + to open a tab.',
  emptyTitle: 'No open tabs',
  noFolderBody: 'Bind a folder to this agent.',
  noFolderTitle: 'This agent has no folder'
}

const files: RightRailTabDef = { icon: 'folder-opened', id: 'files', title: 'Workspace files' }
const agenda: RightRailTabDef = { icon: 'calendar', id: 'agenda', title: 'Agenda' }
const review: RightRailTabDef = { icon: 'git-pull-request', id: 'review', title: 'Review' }

function Host({ initialTabs = [files, agenda] }: { initialTabs?: RightRailTabDef[] }) {
  const [tabs, setTabs] = useState(initialTabs)
  const [active, setActive] = useState<null | RightRailTab>(initialTabs[0]?.id ?? null)
  const available = [files, agenda, review].filter(tab => !tabs.some(open => open.id === tab.id))

  return (
    <RightRailWorkbench
      activeTab={active}
      availableTabs={available}
      contents={{
        agenda: <div data-testid="agenda-body">agenda</div>,
        files: <div data-testid="files-body">files</div>,
        review: <div data-testid="review-body">review</div>
      }}
      labels={labels}
      onTabChange={setActive}
      onTabClose={tab => {
        setTabs(current => current.filter(item => item.id !== tab))
        if (active === tab) setActive(null)
      }}
      onTabOpen={tab => {
        const def = [files, agenda, review].find(item => item.id === tab)
        if (def && !tabs.some(item => item.id === tab)) setTabs(current => [...current, def])
        setActive(tab)
      }}
      tabs={tabs}
    />
  )
}

afterEach(cleanup)

describe('RightRailWorkbench', () => {
  it('switches tabs and keeps panels addressable', () => {
    render(<Host />)

    expect(screen.getByRole('tab', { name: 'Workspace files' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('right-rail-panel-files').hidden).toBe(false)
    expect(screen.getByTestId('right-rail-panel-agenda').hidden).toBe(true)

    fireEvent.click(screen.getByRole('tab', { name: 'Agenda' }))

    expect(screen.getByRole('tab', { name: 'Agenda' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('right-rail-panel-files').hidden).toBe(true)
    expect(screen.getByTestId('right-rail-panel-agenda').hidden).toBe(false)
  })

  it('closes a tab from its leading control without activating it', () => {
    const onClose = vi.fn()

    render(
      <RightRailWorkbench
        activeTab="agenda"
        availableTabs={[]}
        contents={{}}
        labels={labels}
        onTabChange={vi.fn()}
        onTabClose={onClose}
        onTabOpen={vi.fn()}
        tabs={[files, agenda]}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Close Workspace files' }))
    expect(onClose).toHaveBeenCalledWith('files')
  })

  it('opens an available tab from the + menu', () => {
    const onOpen = vi.fn()

    render(
      <RightRailWorkbench
        activeTab="files"
        availableTabs={[review]}
        contents={{}}
        labels={labels}
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        onTabOpen={onOpen}
        tabs={[files]}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open tab' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Review' }))
    expect(onOpen).toHaveBeenCalledWith('review')
  })

  it('shows the empty state when every tab is closed', () => {
    render(
      <RightRailWorkbench
        activeTab={null}
        availableTabs={[files, agenda]}
        contents={{}}
        labels={labels}
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        onTabOpen={vi.fn()}
        tabs={[]}
      />
    )

    expect(screen.getByText(labels.emptyTitle)).toBeTruthy()
    expect(screen.getByText(labels.emptyBody)).toBeTruthy()
  })
})
