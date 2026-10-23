import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { RightRailWorkbench, type RightRailTab, type RightRailWorkbenchLabels } from './right-rail'

const labels: RightRailWorkbenchLabels = {
  aria: 'Right rail',
  noFolderBody: 'Bind a folder to this agent to browse its files here.',
  noFolderTitle: 'This agent has no folder',
  tabs: { agenda: 'Agenda', files: 'Workspace files' }
}

function Host({ files = <div data-testid="files-body">files</div> }: { files?: null | React.ReactNode }) {
  const [tab, setTab] = useState<RightRailTab>('files')

  return (
    <RightRailWorkbench
      activeTab={tab}
      agenda={<div data-testid="agenda-body">agenda</div>}
      files={files}
      labels={labels}
      onTabChange={setTab}
    />
  )
}

afterEach(cleanup)

describe('RightRailWorkbench', () => {
  it('switches between the files and agenda tabs', () => {
    render(<Host />)

    expect(screen.getByTestId('right-rail-tab-files').getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('right-rail-panel-files').hidden).toBe(false)
    expect(screen.getByTestId('right-rail-panel-agenda').hidden).toBe(true)

    fireEvent.click(screen.getByTestId('right-rail-tab-agenda'))

    expect(screen.getByTestId('right-rail-tab-agenda').getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('right-rail-panel-files').hidden).toBe(true)
    expect(screen.getByTestId('right-rail-panel-agenda').hidden).toBe(false)
    expect(screen.getByTestId('agenda-body')).toBeTruthy()
  })

  it('shows the no-folder message instead of an empty tree', () => {
    render(<Host files={null} />)

    expect(screen.getByText(labels.noFolderTitle)).toBeTruthy()
    expect(screen.getByText(labels.noFolderBody)).toBeTruthy()
    expect(screen.queryByTestId('files-body')).toBeNull()
  })
})
