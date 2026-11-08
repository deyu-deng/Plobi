import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSub,
  DropdownMenuSubTrigger
} from '@/components/ui/dropdown-menu'
import { $modelPresets, getModelPreset } from '@/store/model-presets'
import { $activeSessionId } from '@/store/session'

import { type FastControl, ModelEditSubmenu } from './model-edit-submenu'

// Radix calls these on open; jsdom doesn't implement them.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

beforeEach(() => {
  $modelPresets.set({})
  $activeSessionId.set(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// Render the submenu inside an open menu/sub so its content (switches) mounts.
function renderSubmenu(opts: { fastControl: FastControl; reasoning: boolean; requestGateway: () => Promise<unknown> }) {
  return render(
    <DropdownMenu open>
      <DropdownMenuContent>
        <DropdownMenuSub open>
          <DropdownMenuSubTrigger>edit</DropdownMenuSubTrigger>
          <ModelEditSubmenu
            effort="medium"
            fastControl={opts.fastControl}
            isActive
            model="m1"
            onSelectModel={vi.fn()}
            provider="p1"
            reasoning={opts.reasoning}
            requestGateway={opts.requestGateway as never}
          />
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// Regression: editing the active row before a live session exists must stay
// preset-only — the gateway's config.set falls back to global config when no
// session matches, so it must not be called. (Caught in the second review.)
describe('ModelEditSubmenu no-session guard', () => {
  it('param fast: records the preset but skips the gateway without a session', () => {
    const requestGateway = vi.fn().mockResolvedValue({})
    renderSubmenu({ fastControl: { kind: 'param', on: false }, reasoning: false, requestGateway })

    fireEvent.click(screen.getByRole('switch'))

    expect(getModelPreset('p1', 'm1').fast).toBe(true)
    expect(requestGateway).not.toHaveBeenCalled()
  })

  it('reasoning: records the preset but skips the gateway without a session', () => {
    const requestGateway = vi.fn().mockResolvedValue({})
    renderSubmenu({ fastControl: { kind: 'none' }, reasoning: true, requestGateway })

    // Thinking starts on (medium); toggling it off routes through patchReasoning.
    fireEvent.click(screen.getByRole('switch'))

    expect(getModelPreset('p1', 'm1').effort).toBe('none')
    expect(requestGateway).not.toHaveBeenCalled()
  })

  it('param fast: pushes to the gateway once a session is active', async () => {
    const requestGateway = vi.fn().mockResolvedValue({})
    $activeSessionId.set('sess1')
    renderSubmenu({ fastControl: { kind: 'param', on: false }, reasoning: false, requestGateway })

    fireEvent.click(screen.getByRole('switch'))

    expect(requestGateway).toHaveBeenCalledWith('config.set', { key: 'fast', session_id: 'sess1', value: 'fast' })
  })
})

// The vendor names the levels a model really has; the popover may not offer more.
// Measured 2026-11-01: Antigravity sells Claude Opus 5.5 at low/medium/high (its
// `fetchAvailableModels` writes three rows, which the hub now folds into one model), and
// Qoder sells Qwen3.8-Max at low/medium/xhigh. "Max" for a model with no such rung is a
// promise the turn cannot keep, and it was the user's complaint about the ladder.
describe('ModelEditSubmenu effort ladder per model', () => {
  function renderWith(opts: { defaultEffort?: string; efforts?: string[]; effort: string }) {
    return render(
      <DropdownMenu open>
        <DropdownMenuContent>
          <DropdownMenuSub open>
            <DropdownMenuSubTrigger>edit</DropdownMenuSubTrigger>
            <ModelEditSubmenu
              defaultEffort={opts.defaultEffort}
              efforts={opts.efforts}
              effort={opts.effort}
              fastControl={{ kind: 'none' }}
              isActive
              model="antigravity/claude-opus-5-5"
              onSelectModel={vi.fn()}
              provider="quota-row"
              reasoning
              requestGateway={vi.fn().mockResolvedValue({}) as never}
            />
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
    )
  }

  it('offers exactly the three levels this model sells', () => {
    renderWith({ effort: 'medium', efforts: ['low', 'medium', 'high'] })

    const radios = within(document.body).getAllByRole('menuitemradio')
    const levels = radios.filter(node => /Minimal|Max/.test(node.textContent ?? ''))

    expect(levels).toHaveLength(0)
    expect(within(document.body).getAllByRole('menuitemradio')).toHaveLength(3)
  })

  it('keeps the whole ladder for a model that said nothing', () => {
    renderWith({ effort: 'medium' })

    expect(within(document.body).getAllByRole('menuitemradio')).toHaveLength(5)
  })

  it('shows no level selected when the stored preset is one this model cannot run', () => {
    renderWith({ effort: 'xhigh', efforts: ['low', 'medium', 'high'] })

    const checked = within(document.body)
      .getAllByRole('menuitemradio')
      .filter(node => node.getAttribute('aria-checked') === 'true')

    expect(checked).toHaveLength(0)
  })

  it('turning thinking on uses the model’s own default level', () => {
    const requestGateway = vi.fn().mockResolvedValue({})

    $activeSessionId.set('ses-1')

    render(
      <DropdownMenu open>
        <DropdownMenuContent>
          <DropdownMenuSub open>
            <DropdownMenuSubTrigger>edit</DropdownMenuSubTrigger>
            <ModelEditSubmenu
              defaultEffort="xhigh"
              effort="none"
              efforts={['low', 'medium', 'xhigh']}
              fastControl={{ kind: 'none' }}
              isActive
              model="qoder/qwen3.8-max"
              onSelectModel={vi.fn()}
              provider="quota-row"
              reasoning
              requestGateway={requestGateway as never}
            />
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    const thinking = within(document.body)
      .getAllByRole('switch')
      .find(node => (node.closest('[role="menuitem"]')?.textContent ?? '').includes('Thinking'))

    fireEvent.click(thinking as HTMLElement)

    // The session-scoped write is the whole effect: the level the vendor defaults to,
    // never Plobi's generic middle on a model that also sells it.
    expect(requestGateway).toHaveBeenCalledWith('config.set', expect.objectContaining({ value: 'xhigh' }))
  })
})

// 用户 2026-11-08 第 4 条：厂商给了上下文档位的模型，悬浮框里要真能调。目录里 `tuning.context`
// 就是那几个值（Qoder 200K/400K/1M），这里钉住"只有可调且多于一档时才画、点了把数字报上去"。
describe('ModelEditSubmenu window menu', () => {
  it('offers exactly the windows the model sells and reports the pick', () => {
    const onContext = vi.fn()

    render(
      <DropdownMenu open>
        <DropdownMenuContent>
          <DropdownMenuSub open>
            <DropdownMenuSubTrigger>edit</DropdownMenuSubTrigger>
            <ModelEditSubmenu
              context={{ adjustable: true, choices: [200000, 400000, 1000000], default: 200000 }}
              contextValue={200000}
              effort="medium"
              fastControl={{ kind: 'none' }}
              isActive
              model="qoder/qwen3.8-max"
              onContext={onContext}
              onSelectModel={vi.fn()}
              provider="hub-row"
              reasoning
              requestGateway={vi.fn().mockResolvedValue({}) as never}
            />
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    const labels = within(document.body)
      .getAllByRole('menuitemradio')
      .map(node => node.textContent)

    expect(labels).toEqual(expect.arrayContaining(['200K', '400K', '1M']))

    fireEvent.click(within(document.body).getByRole('menuitemradio', { name: '400K' }))

    expect(onContext).toHaveBeenCalledWith(400000)
  })

  it('draws nothing when the model does not sell a menu', () => {
    render(
      <DropdownMenu open>
        <DropdownMenuContent>
          <DropdownMenuSub open>
            <DropdownMenuSubTrigger>edit</DropdownMenuSubTrigger>
            <ModelEditSubmenu
              context={{ max: 200000 }}
              effort="medium"
              fastControl={{ kind: 'none' }}
              isActive
              model="qoder/auto"
              onContext={vi.fn()}
              onSelectModel={vi.fn()}
              provider="hub-row"
              reasoning
              requestGateway={vi.fn().mockResolvedValue({}) as never}
            />
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    // Only the five effort levels — no token numbers dressed up as choices.
    expect(within(document.body).queryByRole('menuitemradio', { name: '400K' })).toBeNull()
  })

  it('shows the windows for a model that sells windows but no thinking levels', () => {
    // 用户 11-08 第 4 条只对了一半：窗口那一组原本挂在「这个模型支不支持思考」上，
    // 于是「有窗口、但没有思考档」的模型既看不到窗口，整个悬浮框还只写一句
    // 「没有可用选项」。窗口与思考是两个独立的旋钮，各按各的真值说话。
    render(
      <DropdownMenu open>
        <DropdownMenuContent>
          <DropdownMenuSub open>
            <DropdownMenuSubTrigger>edit</DropdownMenuSubTrigger>
            <ModelEditSubmenu
              context={{ adjustable: true, choices: [200000, 400000], default: 200000 }}
              contextValue={200000}
              effort="medium"
              fastControl={{ kind: 'none' }}
              isActive
              model="opencode_api/glm-5.3"
              onContext={vi.fn()}
              onSelectModel={vi.fn()}
              provider="hub-row"
              reasoning={false}
              requestGateway={vi.fn().mockResolvedValue({}) as never}
            />
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    const labels = within(document.body).getAllByRole('menuitemradio').map(node => node.textContent)

    expect(labels).toEqual(expect.arrayContaining(['200K', '400K']))
    // 有窗口可调，就不是「没有可用选项」那一句。
    expect(within(document.body).queryByText('No options for this model')).toBeNull()
  })
})
