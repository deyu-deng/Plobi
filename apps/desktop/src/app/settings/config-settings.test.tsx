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

// 外脑那一族的两块（连接流程与各家字段编辑器）各自要打后端；这一档只关心
// **那一行渲染的是后端给的清单** ⇒ 那两块在这里被摘掉，免得它们把树掀了。
vi.mock('./memory/connect', () => ({ MemoryConnect: () => null }))
vi.mock('./provider-config-panel', () => ({ ProviderConfigPanel: () => null }))

async function renderSection(sectionId: string) {
  const { ConfigSettings } = await import('./config-settings')
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConfigSettings activeSectionId={sectionId} importInputRef={{ current: null }} />
    </QueryClientProvider>
  )
}

const renderVoiceSection = () => renderSection('voice')

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

  it('keeps a value the backend’s list does not offer visible', async () => {
    backend.schema = {
      fields: {
        // The backend's own static list is not authoritative for this key
        // (it never had ``mistral``), and the screen no longer keeps a
        // second copy — so the value the user actually has must still show.
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

  it('renders the product enumerations from the backend schema', async () => {
    // The product's own enumerations used to live in a second table on this
    // side; they come from the backend schema now.  ``max`` is the tell: the
    // old local table never had it (it was missing from
    // ``VALID_REASONING_EFFORTS``), so a screen still reading that table
    // could not show the value it was set to.
    backend.schema = {
      fields: {
        'delegation.reasoning_effort': {
          type: 'select',
          description: '',
          options: ['', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
        }
      },
      category_order: ['general']
    }
    backend.config = { delegation: { reasoning_effort: 'max' } }

    await renderSection('advanced')

    await waitFor(() => expect(triggerFor('delegation.reasoning_effort')).toBeTruthy())
    expect(triggerFor('delegation.reasoning_effort').textContent).toContain('Max')
  })

  it('renders the external-memory providers the backend lists', async () => {
    // 2026-11-25（`WP-EXOCORTEX-ENTRY`）：`memory.provider` 当年被后端 schema 跳过了
    // ⇒ 桌面屏上**根本没有这一行** ⇒ 后端那 8+ 家外脑一个都选不到（前端那份手抄
    // 三项是死代码）。现在那一行存在，选项＝**后端给的那一份**。
    backend.schema = {
      fields: {
        'memory.provider': {
          type: 'select',
          description: '',
          options_source: 'registry',
          options: ['', 'byterover', 'hindsight'],
          option_labels: {
            '': 'Off - built-in memory only',
            byterover: 'ByteRover',
            hindsight: 'Hindsight'
          }
        }
      },
      category_order: ['memory']
    }
    backend.config = { memory: { provider: 'byterover' } }

    await renderSection('memory')

    await waitFor(() => expect(triggerFor('memory.provider')).toBeTruthy())
    // 品牌名用后端给的那份（插件自己声明的），不是从下划线猜出来的。
    expect(triggerFor('memory.provider').textContent).toContain('ByteRover')
  })
})
