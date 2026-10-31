import { atom } from 'nanostores'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const STORAGE_KEY = 'plobi.desktop.terminals.v1'

async function loadTerminalStore() {
  const scope = atom('l1')

  vi.doMock('@/store/agent-scope', () => ({ $agentScope: scope }))
  vi.doMock('@/store/session', () => ({
    $currentCwd: atom('/workspace')
  }))

  const mod = await import('./terminals')

  return { ...mod, $agentScope: scope }
}

describe('terminal store persistence', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  it('restores user tabs, active tab, and history on module load', async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        activeTerminalId: 'term-two',
        terminals: [
          { auto: false, cwd: '/repo/one', id: 'term-one', reviveBuffer: 'last output', title: 'zsh' },
          { auto: true, cwd: '/repo/two', id: 'term-two', title: 'Terminal' }
        ]
      })
    )

    const { $activeTerminalId, $terminals } = await loadTerminalStore()

    // A pre-scope archive belonged to the L1 window — folded there, not dropped.
    expect($activeTerminalId.get()).toBe('term-two')
    expect($terminals.get()).toEqual([
      {
        auto: false,
        cwd: '/repo/one',
        id: 'term-one',
        kind: 'user',
        reviveBuffer: 'last output',
        scope: 'l1',
        title: 'zsh'
      },
      { auto: true, cwd: '/repo/two', id: 'term-two', kind: 'user', scope: 'l1', title: 'Terminal' }
    ])
  })

  it('persists user tabs and history synchronously, skipping agent mirrors', async () => {
    const { createTerminal, ensureAgentTerminal, renameTerminal, selectTerminal, updateTerminalReviveBuffer } =
      await loadTerminalStore()

    const userId = createTerminal('/repo')
    renameTerminal(userId, 'server')
    updateTerminalReviveBuffer(userId, 'recent scrollback')
    ensureAgentTerminal('proc-1', 'background task')
    selectTerminal(userId)

    // No flush/tick: persistence is synchronous, so the snapshot is already on
    // disk (this is what makes app-quit restore reliable).
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}')).toEqual({
      activeByScope: { l1: userId },
      terminals: [
        { auto: false, cwd: '/repo', id: userId, reviveBuffer: 'recent scrollback', scope: 'l1', title: 'server' }
      ]
    })
  })

  it('never attaches a revive buffer to an agent tab', async () => {
    const { $terminals, ensureAgentTerminal, updateTerminalReviveBuffer } = await loadTerminalStore()

    const agentId = ensureAgentTerminal('proc-1', 'background task')!
    updateTerminalReviveBuffer(agentId, 'should be ignored')

    expect($terminals.get().find(term => term.id === agentId)?.reviveBuffer).toBeUndefined()
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('tail-trims an oversized revive buffer to stay under the storage budget', async () => {
    const { $terminals, createTerminal, updateTerminalReviveBuffer } = await loadTerminalStore()

    const userId = createTerminal('/repo')
    const huge = 'x'.repeat(60_000)
    updateTerminalReviveBuffer(userId, huge)

    const stored = $terminals.get().find(term => term.id === userId)?.reviveBuffer ?? ''
    expect(stored.length).toBe(48_000)
    expect(stored).toBe(huge.slice(-48_000))
  })

  it('clears remembered tabs when all terminals close', async () => {
    const { closeAllTerminals, createTerminal } = await loadTerminalStore()

    createTerminal('/repo')
    expect(window.localStorage.getItem(STORAGE_KEY)).not.toBeNull()

    closeAllTerminals()
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
  })
})

describe('terminal scope (one Agent, one strip)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  it('shows only the current Agent tabs, and keeps the other Agent selection', async () => {
    const { $agentScope, $activeTerminalId, $scopeTerminals, createTerminal } = await loadTerminalStore()

    $agentScope.set('l2-aura')
    const aura = createTerminal('/repo/aura')

    $agentScope.set('l2-kit')
    const kit = createTerminal('/repo/kit')

    expect($scopeTerminals.get().map(term => term.id)).toEqual([kit])
    expect($activeTerminalId.get()).toBe(kit)

    $agentScope.set('l2-aura')
    expect($scopeTerminals.get().map(term => term.id)).toEqual([aura])
    expect($activeTerminalId.get()).toBe(aura)

    // Both shells stay alive — switching Agents only swaps the strip.
    expect($scopeTerminals.get()).toHaveLength(1)
  })

  it('scopes close-all to the current Agent', async () => {
    const { $agentScope, $terminals, closeAllTerminals, createTerminal } = await loadTerminalStore()

    $agentScope.set('l2-aura')
    const aura = createTerminal('/repo/aura')

    $agentScope.set('l2-kit')
    createTerminal('/repo/kit')
    closeAllTerminals()

    expect($terminals.get().map(term => term.id)).toEqual([aura])
  })

  it('scopes close-others to the current Agent', async () => {
    const { $agentScope, $terminals, closeOtherTerminals, createTerminal } = await loadTerminalStore()

    $agentScope.set('l2-aura')
    const aura = createTerminal('/repo/aura')

    $agentScope.set('l2-kit')
    const kitOne = createTerminal('/repo/kit')
    createTerminal('/repo/kit-two')
    closeOtherTerminals(kitOne)

    expect($terminals.get().map(term => term.id)).toEqual([aura, kitOne])
  })

  it('persists each Agent selection and restores it', async () => {
    const first = await loadTerminalStore()

    first.$agentScope.set('l2-aura')
    const aura = first.createTerminal('/repo/aura')
    first.$agentScope.set('l2-kit')
    const kit = first.createTerminal('/repo/kit')

    vi.resetModules()
    const second = await loadTerminalStore()

    second.$agentScope.set('l2-aura')
    expect(second.$activeTerminalId.get()).toBe(aura)

    second.$agentScope.set('l2-kit')
    expect(second.$activeTerminalId.get()).toBe(kit)
  })
})
