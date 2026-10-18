import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Agent } from '@/app/console/types'
import type { ModelOptionProvider } from '@/types/plobi'

import { AgentModelPicker, flattenModelOptions, FOLLOW_DEFAULT } from './agent-model-picker'

afterEach(cleanup)

const copy = { followDefault: '跟随默认', following: (model: string) => `现在跟的是 ${model}` }

function agent(id: string, model?: string): Agent {
  return { id, model, name: id, status: 'idle', todayCalls: 0 } as Agent
}

function provider(slug: string, models: string[]): ModelOptionProvider {
  return { models, name: slug, slug } as ModelOptionProvider
}

describe('AgentModelPicker', () => {
  it('one row per 分身, and 跟随默认 is the first option with the empty value', () => {
    render(
      <AgentModelPicker
        agents={[agent('aura'), agent('kit')]}
        copy={copy}
        onPick={vi.fn()}
        overrides={{}}
        providers={[provider('workbuddy', ['hy3'])]}
      />
    )

    const selects = screen.getAllByRole('combobox')

    expect(selects).toHaveLength(2)

    for (const select of selects) {
      const [first] = Array.from((select as HTMLSelectElement).options)

      expect(first?.value).toBe(FOLLOW_DEFAULT)
      expect(first?.textContent).toBe(copy.followDefault)
    }
  })

  it('a row without an override sits on 跟随默认 and says who it is following', () => {
    render(
      <AgentModelPicker
        agents={[agent('aura', 'workbuddy/hy3')]}
        copy={copy}
        onPick={vi.fn()}
        overrides={{}}
        providers={[provider('workbuddy', ['hy3'])]}
      />
    )

    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe(FOLLOW_DEFAULT)
    expect(screen.getByText(copy.following('workbuddy/hy3'))).toBeTruthy()
  })

  it('a row with an override shows that override, not the effective model', () => {
    render(
      <AgentModelPicker
        agents={[agent('aura', 'workbuddy/hy3')]}
        copy={copy}
        onPick={vi.fn()}
        overrides={{ aura: 'openai/gpt-5.6' }}
        providers={[provider('openai', ['gpt-5.6'])]}
      />
    )

    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('openai/gpt-5.6')
    expect(screen.queryByText(copy.following('workbuddy/hy3'))).toBeNull()
  })

  it('lists exactly the catalog it was handed — it never pulls a second one', () => {
    const { rerender } = render(
      <AgentModelPicker
        agents={[agent('aura')]}
        copy={copy}
        onPick={vi.fn()}
        overrides={{}}
        providers={[provider('workbuddy', ['hy3', 'step-5-preview'])]}
      />
    )

    expect(
      Array.from((screen.getByRole('combobox') as HTMLSelectElement).options).map(
        option => (option as HTMLOptionElement).value
      )
    ).toEqual([
      FOLLOW_DEFAULT,
      'workbuddy/hy3',
      'workbuddy/step-5-preview'
    ])

    rerender(
      <AgentModelPicker
        agents={[agent('aura')]}
        copy={copy}
        onPick={vi.fn()}
        overrides={{}}
        providers={[provider('aigw', ['deepseek-chat'])]}
      />
    )

    expect(
      Array.from((screen.getByRole('combobox') as HTMLSelectElement).options).map(
        option => (option as HTMLOptionElement).value
      )
    ).toEqual([
      FOLLOW_DEFAULT,
      'aigw/deepseek-chat'
    ])
  })

  it('picking fires once, for that row only, and 跟随默认 comes back as null', () => {
    const onPick = vi.fn()

    render(
      <AgentModelPicker
        agents={[agent('aura'), agent('kit')]}
        copy={copy}
        onPick={onPick}
        overrides={{}}
        providers={[provider('workbuddy', ['hy3'])]}
      />
    )

    const [aura, kit] = screen.getAllByRole('combobox')

    fireEvent.change(kit as HTMLSelectElement, { target: { value: 'workbuddy/hy3' } })
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenLastCalledWith({ agentId: 'kit', model: 'workbuddy/hy3' })

    fireEvent.change(kit as HTMLSelectElement, { target: { value: FOLLOW_DEFAULT } })
    expect(onPick).toHaveBeenLastCalledWith({ agentId: 'kit', model: null })
    expect((aura as HTMLSelectElement).value).toBe(FOLLOW_DEFAULT)
  })
})

describe('flattenModelOptions', () => {
  it('qualifies every model with its provider and drops duplicates', () => {
    expect(
      flattenModelOptions([provider('workbuddy', ['hy3', 'hy3']), provider('aigw', ['deepseek-chat'])])
    ).toEqual(['workbuddy/hy3', 'aigw/deepseek-chat'])
  })

  it('skips a provider that surfaced no models instead of inventing a row', () => {
    expect(flattenModelOptions([{ name: '空', slug: 'empty' } as ModelOptionProvider])).toEqual([])
  })
})
