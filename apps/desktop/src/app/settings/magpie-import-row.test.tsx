import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MagpieImportRow } from './magpie-import-row'

// The row talks to the gateway through the prop; the store helpers it also imports
// would open a real socket, which a jsdom test must not do.
vi.mock('@/store/gateway', () => ({
  activeGateway: () => null,
  ensureActiveGatewayOpen: () => Promise.resolve(null)
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      onboarding: {
        importExisting: 'Import what is already here',
        importExistingDone: 'Imported this machine’s setup',
        importExistingFailed: 'Import failed. Nothing was changed.',
        importExistingNoSubscription: 'No paid subscription was found — add a key to start.',
        importExistingNothing: 'Nothing to import',
        importExistingPitch: 'Already signed in elsewhere? Bring that setup over.',
        importExistingRunning: 'Importing…'
      }
    }
  })
}))

const scan = (importable: boolean) =>
  Promise.resolve({
    imported_from: null,
    sources: [
      {
        dirs: [{ bytes: 10, name: 'plugins' }],
        files: [{ bytes: 5, name: 'logins.json' }],
        importable,
        path: 'C:/Users/someone/.config/magpie'
      }
    ],
    target: 'D:/Data/AppData/Plobi-Dev/magpie'
  })

function bridge(over: Partial<Record<'restartMagpie', unknown>> = {}) {
  const restartMagpie = (over.restartMagpie as ReturnType<typeof vi.fn>) ?? vi.fn().mockResolvedValue({ port: 1, reason: null, started: true })

  window.plobiDesktop = { restartMagpie } as never

  return restartMagpie
}

beforeEach(() => {
  delete (window as { plobiDesktop?: unknown }).plobiDesktop
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  delete (window as { plobiDesktop?: unknown }).plobiDesktop
})

describe('MagpieImportRow', () => {
  it('does not ask a gateway that is not there', async () => {
    // A browser preview (and every unit test) has no bridge. Asking anyway opens a
    // socket to nobody, and the only answer is an error nobody sees.
    const requestGateway = vi.fn()

    render(<MagpieImportRow requestGateway={requestGateway} />)

    await waitFor(() => expect(screen.queryByTestId('magpie-import-row')).toBeNull())
    expect(requestGateway).not.toHaveBeenCalled()
  })

  it('stays away when this machine has nothing to bring over', async () => {
    bridge()

    const requestGateway = vi.fn(() => scan(false))

    render(<MagpieImportRow requestGateway={requestGateway as never} />)

    await waitFor(() => expect(requestGateway).toHaveBeenCalledWith('magpie.import_scan'))
    expect(screen.queryByTestId('magpie-import-row')).toBeNull()
  })

  it('offers the import once a source can actually be imported', async () => {
    bridge()

    const requestGateway = vi.fn(() => scan(true))

    render(<MagpieImportRow requestGateway={requestGateway as never} />)

    expect(await screen.findByText('Import what is already here')).toBeTruthy()
    // The promise, not the button, has to say what it does — otherwise the row reads
    // like "import a file" and the user cannot tell what a copy costs them.
    expect(screen.getByText(/Bring that setup over/)).toBeTruthy()
  })

  it('restarts the sidecar after copying, because magpie reads logins at boot', async () => {
    const restartMagpie = bridge()
    const onImported = vi.fn()
    const requestGateway = vi.fn((method: string) =>
      method === 'magpie.import_scan'
        ? scan(true)
        : Promise.resolve({ copied: 3, dirs: ['plugins'], error: null, files: ['logins.json'], from: 'x', ok: true, provider_configured: true, subscription: true })
    )

    render(<MagpieImportRow onImported={onImported} requestGateway={requestGateway as never} />)

    fireEvent.click(await screen.findByText('Import what is already here'))

    await waitFor(() => expect(restartMagpie).toHaveBeenCalledTimes(1))
    expect(requestGateway).toHaveBeenCalledWith('magpie.import_apply', {})
    expect(onImported).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Imported this machine’s setup')).toBeTruthy()
  })

  it('says out loud when the copy landed but no allowance came with it', async () => {
    bridge()

    const requestGateway = vi.fn((method: string) =>
      method === 'magpie.import_scan'
        ? scan(true)
        : Promise.resolve({ copied: 1, dirs: [], error: null, files: ['plugins.json'], from: 'x', ok: true, provider_configured: false, subscription: false })
    )

    render(<MagpieImportRow requestGateway={requestGateway as never} />)

    fireEvent.click(await screen.findByText('Import what is already here'))

    // 用户 2026-11-09：订阅额度也算"配置好了"的一种。反过来，导入到一份没有额度的
    // 配置时必须说清楚，否则他以为已经能发消息了。
    expect(await screen.findByText(/No paid subscription was found/)).toBeTruthy()
  })

  it('reports a failed import instead of leaving the button spinning', async () => {
    bridge()

    const requestGateway = vi.fn((method: string) => (method === 'magpie.import_scan' ? scan(true) : Promise.reject(new Error('boom'))))

    render(<MagpieImportRow requestGateway={requestGateway as never} />)

    fireEvent.click(await screen.findByText('Import what is already here'))

    expect(await screen.findByText(/Import failed/)).toBeTruthy()
  })
})
