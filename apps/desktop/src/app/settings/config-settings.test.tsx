/**
 * The provider dropdown must offer what the backend has.
 *
 * `WP-SCREEN-OPTIONS-REGISTRY` — the screen used to read its own hardcoded
 * table (`constants.ts`), so a provider the backend knows about but that
 * table never heard of — a plugin-registered TTS backend — was installed
 * and *not selectable* ("插件装上了、屏上却选不到").
 *
 * The backend now sends the live option list on the schema field itself
 * (`options_source: 'registry'` ＋ `options` ＋ `option_labels`), and the
 * screen renders that.  These tests pin the two halves of the contract:
 *
 *  1. a registry-backed key renders **the backend's** options and labels;
 *  2. a key **without** a registry behind it still renders the screen's own
 *     table — the backend's static option lists are not authoritative (they
 *     drift), so the fallback must not die with this change.
 *
 * They read the *selected value's* label off the trigger instead of opening
 * the dropdown: a Radix select in jsdom is a pointer-event minefield, and
 * the trigger already shows the label the screen would list.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => cleanup())

const backend = vi.hoisted(() => ({
  schema: { fields: {} as Record<string, unknown>, category_order: ['general'] },
  config: {} as Record<string, unknown>
}))

vi.mock('@/i18n', async () => {
  const mod = await import('@/i18n/en')
  return { useI18n: () => ({ locale: 'en', t: mod.en }) }
})

vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(), () => undefined]
}))

vi.mock('../hooks/use-config-record', () => ({
  usePlobiConfigRecord: () => ({ data: backend.config, isError: false, refetch: () => undefined }),
  setPlobiConfigCache: () => undefined
}))

vi.mock('@/plobi', async importOriginal => {
  const actual = await importOriginal<typeof import('@/plobi')>()
  return {
    ...actual,
    getElevenLabsVoices: async () => [],
    getPlobiConfigSchema: async () => backend.schema,
    savePlobiConfig: async () => ({ ok: true })
  }
})

vi.mock('@/store/notifications', () => ({ notify: () => undefined, notifyError: () => undefined }))

vi.mock('../overlays/panel', () => ({ PanelEmpty: () => null }))

async function renderVoiceSection() {
  const { ConfigSettings } = await import('./config-settings')
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConfigSettings activeSectionId="voice" importInputRef={{ current: null }} />
    </QueryClientProvider>
  )
}

function triggerFor(key: string): HTMLElement {
  const row = document.getElementById(`setting-field-${key}`)
  if (!row) {
    throw new Error(`no row rendered for ${key}`)
  }
  const trigger = within(row).getByRole('combobox')
  return trigger as HTMLElement
}

describe('ConfigSettings · provider dropdown', () => {
  it('renders the option list the backend computed', async () => {
    backend.schema = {
      fields: {
        'tts.provider': {
          type: 'select',
          description: '',
          options_source: 'registry',
          options: ['edge', 'fake-tts'],
          option_labels: { edge: 'Microsoft Edge TTS', 'fake-tts': 'Fake TTS' }
        }
      },
      category_order: ['general']
    }
    backend.config = { tts: { provider: 'fake-tts' } }

    await renderVoiceSection()

    await waitFor(() => expect(triggerFor('tts.provider')).toBeTruthy())
    // The provider the screen's own table never listed is selectable, and it
    // shows the human name the backend sent — not the slug.
    expect(triggerFor('tts.provider').textContent).toContain('Fake TTS')
  })

  it('keeps the screen table for keys with no registry behind them', async () => {
    backend.schema = {
      fields: {
        // No ``options_source`` ⇒ this key has no registry behind it, and the
        // backend's own static list is stale (it never had ``mistral``).
        'stt.provider': {
          type: 'select',
          description: '',
          options: ['local', 'groq', 'openai', 'xai', 'elevenlabs']
        }
      },
      category_order: ['general']
    }
    backend.config = { stt: { provider: 'mistral' } }

    await renderVoiceSection()

    await waitFor(() => expect(triggerFor('stt.provider')).toBeTruthy())
    expect(triggerFor('stt.provider').textContent).toContain('Mistral')
  })
})
