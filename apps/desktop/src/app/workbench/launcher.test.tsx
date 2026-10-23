import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WorkbenchLauncher, type WorkbenchLauncherItem } from './launcher'

const items: WorkbenchLauncherItem[] = [
  { icon: 'folder-opened', id: 'files', title: 'Workspace files' },
  { icon: 'git-pull-request', id: 'review', title: 'Review', hint: 'No project' },
  { icon: 'terminal', id: 'terminal', title: 'Terminal' },
  { icon: 'calendar', id: 'agenda', title: 'Agenda' }
]

afterEach(cleanup)

function renderLauncher(overrides: Partial<Parameters<typeof WorkbenchLauncher>[0]> = {}) {
  const onClose = vi.fn()
  const onPick = vi.fn()

  render(
    <WorkbenchLauncher
      items={items}
      labels={{ title: 'Open' }}
      onClose={onClose}
      onPick={onPick}
      open
      {...overrides}
    />
  )

  return { onClose, onPick }
}

describe('WorkbenchLauncher', () => {
  it('renders every entry and reports the picked action', () => {
    const { onPick } = renderLauncher()

    expect(screen.getByRole('dialog', { name: 'Open' })).toBeTruthy()

    for (const item of items) {
      expect(screen.getByTestId(`workbench-launcher-${item.id}`)).toBeTruthy()
    }

    fireEvent.click(screen.getByTestId('workbench-launcher-files'))
    expect(onPick).toHaveBeenCalledWith('files')
  })

  it('keeps disabled entries inert', () => {
    const { onPick } = renderLauncher({
      items: [{ disabled: true, icon: 'git-pull-request', id: 'review', title: 'Review' }]
    })

    fireEvent.click(screen.getByTestId('workbench-launcher-review'))
    expect(onPick).not.toHaveBeenCalled()
  })

  it('closes on Escape and on a backdrop press', () => {
    const { onClose } = renderLauncher()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.mouseDown(screen.getByTestId('workbench-launcher-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('does not close when the panel itself is pressed', () => {
    const { onClose } = renderLauncher()

    fireEvent.mouseDown(screen.getByTestId('workbench-launcher'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('renders nothing while closed', () => {
    render(<WorkbenchLauncher items={items} labels={{ title: 'Open' }} onClose={vi.fn()} onPick={vi.fn()} open={false} />)

    expect(screen.queryByTestId('workbench-launcher')).toBeNull()
  })
})
