import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { BrowserTabPanel, type BrowserTabLabels, normalizeBrowserUrl } from './browser-tab'

const labels: BrowserTabLabels = {
  back: 'Back',
  forward: 'Forward',
  go: 'Open',
  invalidUrl: 'Only http/https URLs are supported',
  loadFailed: 'This page could not be opened',
  newTabBody: 'Type a URL above and press Enter.',
  newTabTitle: 'New tab',
  placeholder: 'Enter a URL',
  reload: 'Reload'
}

afterEach(cleanup)

describe('normalizeBrowserUrl', () => {
  it('adds the right scheme for local and public hosts', () => {
    expect(normalizeBrowserUrl('localhost:3000')).toBe('http://localhost:3000')
    expect(normalizeBrowserUrl('127.0.0.1:5174/app')).toBe('http://127.0.0.1:5174/app')
    expect(normalizeBrowserUrl('example.com')).toBe('https://example.com')
    expect(normalizeBrowserUrl('https://example.com/a')).toBe('https://example.com/a')
  })

  it('rejects non-http schemes', () => {
    expect(normalizeBrowserUrl('javascript:alert(1)')).toBe('')
    expect(normalizeBrowserUrl('file:///etc/passwd')).toBe('')
  })
})

describe('BrowserTabPanel', () => {
  it('starts on the new-tab surface and opens a normalized URL', () => {
    render(<BrowserTabPanel labels={labels} />)

    expect(screen.getByText(labels.newTabTitle)).toBeTruthy()

    const input = screen.getByLabelText(labels.placeholder)
    fireEvent.change(input, { target: { value: 'localhost:3000' } })
    fireEvent.submit(screen.getByTestId('browser-form'))

    expect((input as HTMLInputElement).value).toBe('http://localhost:3000')
    expect(screen.queryByText(labels.newTabTitle)).toBeNull()
    expect(screen.getByTestId('browser-webview-host').hidden).toBe(false)
    expect(document.querySelector('webview')).toBeTruthy()
  })

  it('reports a non-http scheme instead of loading it', () => {
    render(<BrowserTabPanel labels={labels} />)

    fireEvent.change(screen.getByLabelText(labels.placeholder), { target: { value: 'javascript:alert(1)' } })
    fireEvent.submit(screen.getByTestId('browser-form'))

    expect(screen.getByText(labels.invalidUrl)).toBeTruthy()
    expect(screen.getByText(labels.newTabTitle)).toBeTruthy()
    expect(screen.getByTestId('browser-webview-host').hidden).toBe(true)
  })
})
