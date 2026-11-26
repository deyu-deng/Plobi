import { describe, expect, it } from 'vitest'

import type { PlobiConfigRecord } from '@/types/plobi'

import { defineFieldCopy, fieldCopyForSchemaKey, schemaKeyToFieldCopyKey } from './field-copy'
import { enumOptionsFor, getNested, providerGroup, setNested, stripToolsetLabel, toolsetDisplayLabel } from './helpers'

describe('settings helpers', () => {
  it('外脑那一族的选项**走后端那一份**（本地不再留手抄清单）', () => {
    // 2026-11-25：桌面那份手抄的三项清单删掉了 ⇒ 本地表里**查无此键**
    // （`enumOptionsFor` 返回 undefined ⇒ 屏上走后端给的那份动态选项）。
    expect(enumOptionsFor('memory.provider', '', {})).toBeUndefined()
  })

  it('后端给了动态选项时，屏上就渲染那一份', () => {
    const options = enumOptionsFor('memory.provider', '', {}, ['', 'hindsight', 'honcho'])

    expect(options).toContain('hindsight')
  })

  describe('defineFieldCopy', () => {
    it('flattens nested field copy paths', () => {
      const copy = defineFieldCopy({
        display: {
          personality: 'Personality'
        },
        stt: {
          elevenlabs: {
            language_code: 'Language'
          }
        }
      })

      expect(copy[['display', 'personality'].join('.')]).toBe('Personality')
      expect(copy[['stt', 'elevenlabs', 'language_code'].join('.')]).toBe('Language')
    })

    it('keeps top-level flat field keys', () => {
      expect(
        defineFieldCopy({
          model_context_length: 'Context Window',
          file_read_max_chars: 'File Read Limit'
        })
      ).toEqual({
        model_context_length: 'Context Window',
        file_read_max_chars: 'File Read Limit'
      })
    })

    it('maps schema keys to camelCase translation keys', () => {
      expect(schemaKeyToFieldCopyKey('model_context_length')).toBe('modelContextLength')
      expect(schemaKeyToFieldCopyKey('display.show_reasoning')).toBe('display.showReasoning')
      expect(schemaKeyToFieldCopyKey('tool_output.max_line_length')).toBe('toolOutput.maxLineLength')
      expect(schemaKeyToFieldCopyKey('updates.non_interactive_local_changes')).toBe(
        'updates.nonInteractiveLocalChanges'
      )
    })

    it('looks up camelCase field copy by schema key with legacy fallback', () => {
      const copy = defineFieldCopy({
        display: {
          showReasoning: 'Reasoning Blocks'
        },
        file_read_max_chars: 'Legacy File Read Limit',
        modelContextLength: 'Context Window',
        toolOutput: {
          maxLineLength: 'Line Length Limit'
        }
      })

      expect(fieldCopyForSchemaKey(copy, 'model_context_length')).toBe('Context Window')
      expect(fieldCopyForSchemaKey(copy, 'display.show_reasoning')).toBe('Reasoning Blocks')
      expect(fieldCopyForSchemaKey(copy, 'tool_output.max_line_length')).toBe('Line Length Limit')
      expect(fieldCopyForSchemaKey(copy, 'file_read_max_chars')).toBe('Legacy File Read Limit')
    })

    it('rejects duplicate flattened paths', () => {
      const duplicateKey = ['display', 'personality'].join('.')

      expect(() =>
        defineFieldCopy({
          display: {
            personality: 'Personality'
          },
          [duplicateKey]: 'Duplicate'
        })
      ).toThrow('Duplicate field copy key: display.personality')
    })
  })

  it('reads and writes nested config paths', () => {
    const config: PlobiConfigRecord = { display: { theme: 'mono' } }
    const next = setNested(config, 'display.theme', 'slate')

    expect(getNested(next, 'display.theme')).toBe('slate')
    expect(getNested(config, 'display.theme')).toBe('mono')
  })

  it('rejects prototype-polluting config paths', () => {
    const config: PlobiConfigRecord = {}

    expect(() => setNested(config, '__proto__.polluted', true)).toThrow('Unsafe config path')
    expect(() => setNested(config, 'constructor.prototype.polluted', true)).toThrow('Unsafe config path')
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
  })

  describe('stripToolsetLabel', () => {
    it('removes leading emoji prefixes from registry labels', () => {
      expect(stripToolsetLabel('⏰ Cron Jobs')).toBe('Cron Jobs')
      expect(stripToolsetLabel('⚡ Code Execution')).toBe('Code Execution')
      expect(stripToolsetLabel('❓ Clarifying Questions')).toBe('Clarifying Questions')
      expect(stripToolsetLabel('🌐 Browser Automation')).toBe('Browser Automation')
      expect(stripToolsetLabel('🎨 Image Generation')).toBe('Image Generation')
    })

    it('leaves plain titles unchanged', () => {
      expect(stripToolsetLabel('Terminal & Processes')).toBe('Terminal & Processes')
    })
  })

  describe('toolsetDisplayLabel', () => {
    it('strips emoji from toolset rows', () => {
      expect(toolsetDisplayLabel({ name: 'cronjob', label: '⏰ Cron Jobs' })).toBe('Cron Jobs')
    })
  })

  describe('providerGroup', () => {
    it('maps a provider env var to its labeled group', () => {
      expect(providerGroup('XAI_API_KEY')).toBe('xAI')
      expect(providerGroup('NOUS_API_KEY')).toBe('Nous Portal')
      expect(providerGroup('OPENROUTER_API_KEY')).toBe('OpenRouter')
    })

    it('prefers the longest matching prefix so CN/regional buckets win', () => {
      // MINIMAX_CN_ must beat the generic MINIMAX_ prefix.
      expect(providerGroup('MINIMAX_CN_API_KEY')).toBe('MiniMax (China)')
      expect(providerGroup('MINIMAX_API_KEY')).toBe('MiniMax')
      // KIMI_CN_ likewise must beat KIMI_.
      expect(providerGroup('KIMI_CN_API_KEY')).toBe('Kimi (China)')
      expect(providerGroup('KIMI_API_KEY')).toBe('Kimi / Moonshot')
      // PLOBI_QWEN_ shares the PLOBI_ stem with other integrations.
      expect(providerGroup('PLOBI_QWEN_BASE_URL')).toBe('DashScope (Qwen)')
      expect(providerGroup('GEMINI_API_KEY')).toBe('Gemini')
    })

    it('falls back to "Other" for un-grouped env vars', () => {
      expect(providerGroup('SOMETHING_RANDOM')).toBe('Other')
    })
  })

  describe('enumOptionsFor — where a dropdown’s options come from', () => {
    const config: PlobiConfigRecord = {}

    it('leaves the provider pickers and the product enumerations to the backend', () => {
      // Those lists live in the backend schema now (one list per key).
      // This helper must not answer for them: returning undefined lets the
      // screen fall back to the schema's own options.
      expect(enumOptionsFor('tts.provider', 'edge', config)).toBeUndefined()
      expect(enumOptionsFor('stt.provider', 'local', config)).toBeUndefined()
      expect(enumOptionsFor('terminal.backend', 'local', config)).toBeUndefined()
      expect(enumOptionsFor('approvals.mode', 'manual', config)).toBeUndefined()
    })

    it('renders dropdowns for per-backend model/device sub-fields', () => {
      // These are vendor catalogues — the provider's own ids, not the
      // product's vocabulary.
      expect(enumOptionsFor('stt.openai.model', 'whisper-1', config)).toContain('gpt-4o-transcribe')
      expect(enumOptionsFor('tts.openai.model', 'gpt-4o-mini-tts', config)).toContain('tts-1-hd')
      expect(enumOptionsFor('tts.neutts.device', 'cpu', config)).toEqual(['cpu', 'cuda', 'mps'])
    })

    it('appends a hand-typed value not in the known list so it stays selected', () => {
      const opts = enumOptionsFor('tts.openai.model', 'my-own-model', config)
      expect(opts).toContain('my-own-model')
      expect(opts).toContain('tts-1-hd')
    })

    it('answers with the caller’s list when one is passed (the backend’s live list)', () => {
      const opts = enumOptionsFor('tts.provider', 'my-plugin-tts', config, ['edge', 'my-plugin-tts'])
      expect(opts).toEqual(['edge', 'my-plugin-tts'])
    })
  })
})
