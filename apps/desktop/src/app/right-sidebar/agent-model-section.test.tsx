import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RosterAgent } from '@/app/settings/agent-model-overrides'
import { TRANSLATIONS } from '@/i18n/catalog'

const { putAgentModel, refreshConsoleAgents, requestModelOptions } = vi.hoisted(() => ({
  putAgentModel: vi.fn(),
  refreshConsoleAgents: vi.fn(),
  requestModelOptions: vi.fn()
}))

vi.mock('@/app/console/api', () => ({ putAgentModel }))
vi.mock('@/app/console/store/agents', () => ({ refreshConsoleAgents }))
vi.mock('@/lib/model-options', () => ({ requestModelOptions }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ t: TRANSLATIONS.en }) }))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))

import { AgentModelSection } from './agent-model-section'

/**
 * 这一段守的是三件事：读不到标记时**什么都不断**；「跟随默认」与"显式选了同一个值"分得开；
 * 撤回是**两个键都给空串**（不是缺键——后端会把缺键当 400，那是故意的）。
 */

const PROVIDERS = [{ models: ['deepseek-chat', 'k2'], slug: 'deepseek' }] as const

function row(over: Partial<RosterAgent> = {}): RosterAgent {
  return {
    id: 'Aura',
    model: 'deepseek/deepseek-chat',
    name: 'Aura',
    profile: 'aura',
    status: 'idle',
    todayCalls: 0,
    ...over
  }
}

function mount(agent: RosterAgent) {
  requestModelOptions.mockResolvedValue({ providers: [...PROVIDERS] })

  return render(<AgentModelSection agent={agent} />)
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AgentModelSection', () => {
  it('says nothing about the state when the backend did not report the flag', () => {
    mount(row())

    const box = screen.getByTestId('model-state-unknown')

    expect(box.textContent).toContain(TRANSLATIONS.en.rightSidebar.modelStateUnknown)
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('preselects 跟随默认 only for a row that says it carries no override', async () => {
    mount(row({ modelOverride: false }))

    const select = await screen.findByRole('combobox')

    expect((select as HTMLSelectElement).value).toBe('')
    expect(select.parentElement?.parentElement?.textContent).toContain('following deepseek/deepseek-chat')
  })

  it('shows the row\'s own model as an explicit choice when the flag says so', async () => {
    mount(row({ modelOverride: true }))

    const select = (await screen.findByRole('combobox')) as HTMLSelectElement

    expect(select.value).toBe('deepseek/deepseek-chat')
  })

  it('writes an override as provider + model and reloads the roster afterwards', async () => {
    putAgentModel.mockResolvedValue({ cleared: false, model: 'k2', ok: true, provider: 'deepseek' })
    refreshConsoleAgents.mockResolvedValue(undefined)
    mount(row({ modelOverride: false }))

    const select = (await screen.findByRole('combobox')) as HTMLSelectElement

    fireEvent.change(select, { target: { value: 'deepseek/k2' } })

    await waitFor(() => expect(putAgentModel).toHaveBeenCalledWith('aura', { model: 'k2', provider: 'deepseek' }))
    expect(refreshConsoleAgents).toHaveBeenCalled()
  })

  it('clears with both keys present and empty — not by omitting them', async () => {
    putAgentModel.mockResolvedValue({ cleared: true, model: '', ok: true, provider: '' })
    refreshConsoleAgents.mockResolvedValue(undefined)
    mount(row({ modelOverride: true }))

    const select = (await screen.findByRole('combobox')) as HTMLSelectElement

    fireEvent.change(select, { target: { value: '' } })

    await waitFor(() => expect(putAgentModel).toHaveBeenCalledWith('aura', null))
  })

  it('falls back to the roster name when the row carries no profile — same rule the backend resolves by', async () => {
    putAgentModel.mockResolvedValue({ cleared: true, model: '', ok: true, provider: '' })
    refreshConsoleAgents.mockResolvedValue(undefined)
    mount(row({ modelOverride: true, profile: undefined }))

    const select = (await screen.findByRole('combobox')) as HTMLSelectElement

    fireEvent.change(select, { target: { value: '' } })

    await waitFor(() => expect(putAgentModel).toHaveBeenCalledWith('Aura', null))
  })
})
