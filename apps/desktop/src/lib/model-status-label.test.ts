import { describe, expect, it } from 'vitest'

import {
  currentPickerSelection,
  displayModelName,
  formatModelStatusLabel,
  qualifyModelId,
  reasoningEffortLabel
} from './model-status-label'

describe('qualifyModelId', () => {
  it('qualifies a bare catalog id with its provider', () => {
    expect(qualifyModelId('openai', 'gpt-5.5')).toBe('openai/gpt-5.5')
  })
  it('leaves an already-qualified id alone — catalogs are not uniform', () => {
    // Hub software ships `workbuddy/auto`; a blind concat produced
    // `workbuddy/workbuddy/auto`, which no catalog contains, so every switch 404'd.
    expect(qualifyModelId('workbuddy', 'workbuddy/auto')).toBe('workbuddy/auto')
    expect(qualifyModelId('workbuddy', 'workbuddy/deepseek-v4.1-flash')).toBe('workbuddy/deepseek-v4.1-flash')
  })
  it('is idempotent, so a second pass cannot add a second prefix', () => {
    const once = qualifyModelId('workbuddy', 'auto')
    expect(qualifyModelId('workbuddy', once)).toBe(once)
    expect(qualifyModelId('workbuddy', qualifyModelId('workbuddy', once))).toBe(once)
  })
  it('never rewrites a foreign provider prefix', () => {
    expect(qualifyModelId('workbuddy', 'openai/gpt-5.5')).toBe('workbuddy/openai/gpt-5.5')
  })
  it('degrades safely on empty input', () => {
    expect(qualifyModelId('workbuddy', '')).toBe('')
    expect(qualifyModelId('workbuddy', '   ')).toBe('')
    expect(qualifyModelId('', 'auto')).toBe('auto')
    expect(qualifyModelId(' workbuddy ', ' auto ')).toBe('workbuddy/auto')
  })
})

describe('model-status-label', () => {
  it('formats display names consistently', () => {
    expect(displayModelName('anthropic/claude-opus-4.8-fast')).toBe('Opus 4.8')
    expect(displayModelName('openai/gpt-5.5-fast')).toBe('GPT-5.5')
    expect(displayModelName('deepseek/deepseek-v4-pro-thinking')).toBe('Deepseek V4 Pro')
    expect(displayModelName('openai/gpt-5.5')).toBe('GPT-5.5')
    expect(displayModelName('antigravity/claude-opus-5-5-medium')).toBe('Opus 5.5 (Medium)')
    expect(displayModelName('antigravity/gpt-oss-120b-medium')).toBe('GPT-OSS 120B (Medium)')
    expect(displayModelName('antigravity/gemini-3-flash-agent')).toBe('Gemini 3.5 Flash (High)')
    expect(displayModelName('antigravity/gemini-3.5-flash-lite')).toBe('Gemini 3.5 Flash Lite')
    expect(displayModelName('workbuddy/glm-5.3-flash')).toBe('GLM 5.3 Flash')
  })

  it('strips trailing date-pin snapshots from the display name', () => {
    expect(displayModelName('claude-opus-4-5-20251101')).toBe('Opus 4.5')
    expect(displayModelName('anthropic/claude-haiku-4-5-20251001')).toBe('Haiku 4.5')
  })

  it('maps reasoning effort to compact labels', () => {
    expect(reasoningEffortLabel('high')).toBe('High')
    expect(reasoningEffortLabel('xhigh')).toBe('Max')
    expect(reasoningEffortLabel('')).toBe('')
  })

  it('appends fast + effort session state to the status label', () => {
    expect(formatModelStatusLabel('openai/gpt-5.5', { fastMode: true, reasoningEffort: 'high' })).toBe(
      'GPT-5.5 · Fast High'
    )
  })

  it('always surfaces the effort (default medium) so the level is visible', () => {
    expect(formatModelStatusLabel('openai/gpt-5.5', { reasoningEffort: 'medium' })).toBe('GPT-5.5 · Med')
    expect(formatModelStatusLabel('openai/gpt-5.5')).toBe('GPT-5.5 · Med')
  })

  it('returns just the placeholder name when there is no model', () => {
    expect(formatModelStatusLabel('')).toBe('No model')
  })

  describe('currentPickerSelection', () => {
    const store = { model: 'opus', provider: 'anthropic' }
    const options = { model: 'hermes-4', provider: 'nous' }

    it('prefers the sticky composer pick over the profile default pre-session', () => {
      expect(currentPickerSelection(false, store, options)).toEqual(store)
    })

    it('lets the live session model.options win when a session exists', () => {
      expect(currentPickerSelection(true, store, options)).toEqual(options)
    })

    it('falls back to options when the store is empty', () => {
      expect(currentPickerSelection(false, { model: '', provider: '' }, options)).toEqual(options)
    })

    it('falls back to the store while options are still loading', () => {
      expect(currentPickerSelection(true, store, undefined)).toEqual(store)
    })
  })
})
