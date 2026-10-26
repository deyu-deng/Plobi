import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GenOfficeFileEditor } from './genoffice-editor'
import type { OfficePreviewLabels } from './office-preview'

const labels: OfficePreviewLabels = {
  loading: 'Loading file…',
  unavailable: 'This file cannot be previewed',
  readOnly: 'Read-only preview',
  truncated: (rows, cols) => `${rows} × ${cols}`,
  sheet: 'Sheet',
  legacyNote: '',
  save: 'Save',
  saving: 'Saving…',
  saved: 'Saved',
  cancel: 'Cancel',
  conflict: 'File changed on disk',
  saveFailed: 'Save failed',
  reload: 'Reload',
  review: 'Review',
  hint: 'Restart Plobi to load the editor.',
  diskChanged: 'The file changed on disk',
  dismiss: 'Later'
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('GenOfficeFileEditor', () => {
  it('mounts a sandboxed Sheets webview with the sheets preload', async () => {
    const preload = vi.fn().mockResolvedValue('file:///plobi/sheets-preload.cjs')
    vi.stubGlobal('plobiDesktop', { genofficePreloadUrl: preload })

    const { container } = render(
      <GenOfficeFileEditor kind="sheets" labels={labels} path="D:/work/文献汇总表.xlsx" />
    )
    const webview = await waitFor(() => {
      const found = container.querySelector('webview')
      expect(found).toBeTruthy()

      return found!
    })

    expect(preload).toHaveBeenCalledWith('sheets')
    expect(webview.getAttribute('preload')).toBe('file:///plobi/sheets-preload.cjs')
    expect(webview.getAttribute('partition')).toBe('persist:plobi-genoffice-sheets')
    expect(webview.getAttribute('webpreferences')).toContain('nodeIntegration=no')
    expect(webview.getAttribute('src')).toBe(
      'http://127.0.0.1:5175/sheets.html?file=D%3A%2Fwork%2F%E6%96%87%E7%8C%AE%E6%B1%87%E6%80%BB%E8%A1%A8.xlsx'
    )
  })

  it('routes word files to the word editor and retries after a load failure', async () => {
    const preload = vi.fn().mockResolvedValue('file:///plobi/word-preload.cjs')
    vi.stubGlobal('plobiDesktop', { genofficePreloadUrl: preload })

    const { container } = render(
      <GenOfficeFileEditor kind="word" labels={labels} path="D:/work/report.docx" />
    )
    const webview = await waitFor(() => {
      const found = container.querySelector('webview')
      expect(found).toBeTruthy()

      return found!
    })

    expect(preload).toHaveBeenCalledWith('word')
    expect(webview.getAttribute('src')).toBe(
      'http://127.0.0.1:5175/word.html?file=D%3A%2Fwork%2Freport.docx'
    )

    const failure = new Event('did-fail-load')
    Object.assign(failure, { errorCode: -102, isMainFrame: true })
    fireEvent(webview, failure)

    expect(screen.getByText(labels.unavailable)).toBeTruthy()
    expect(screen.getByText(labels.hint)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: labels.reload }))
    await waitFor(() => expect(preload).toHaveBeenCalledTimes(2))
  })
})
