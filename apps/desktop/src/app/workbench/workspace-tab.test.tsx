import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WorkspaceTabPanel, type WorkspaceTabLabels } from './workspace-tab'

const labels: WorkspaceTabLabels = {
  hint: 'Start Univer Workspace first.',
  loading: 'Opening the local Office workspace…',
  retry: 'Retry',
  unavailable: 'The local Office workspace is not reachable.'
}

afterEach(cleanup)

describe('WorkspaceTabPanel', () => {
  it('mounts a sandboxed webview pointed at the local workspace', () => {
    const { container } = render(<WorkspaceTabPanel labels={labels} />)
    const webview = container.querySelector('webview')

    expect(webview?.getAttribute('partition')).toBe('persist:plobi-workspace')
    expect(webview?.getAttribute('webpreferences')).toContain('nodeIntegration=no')
    expect(webview?.getAttribute('src')).toMatch(/^http:\/\/127\.0\.0\.1:(5173|3020)$/u)
    expect(screen.getByLabelText(labels.loading)).toBeTruthy()
  })

  it('reports an unreachable workspace and retries in place', () => {
    const { container } = render(<WorkspaceTabPanel labels={labels} />)
    const webview = container.querySelector('webview') as HTMLElement & { reload?: () => void }
    const reload = vi.fn()
    webview.reload = reload

    const failure = new Event('did-fail-load')
    Object.assign(failure, { errorCode: -102, isMainFrame: true })
    fireEvent(webview, failure)

    expect(screen.getByText(labels.unavailable)).toBeTruthy()
    expect(screen.getByText(labels.hint)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: labels.retry }))
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
