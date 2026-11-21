import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 权限档（每次询问 / 自动审批 / 完全访问）的**记住**（用户 11-21）。
 *
 * 三条规矩，这一档钉的就是它们：
 *
 * 1. **记忆只由用户的动作写**。网关报回来的「生效档」（会话自己的档 ‖ 全局兜底）
 *    只是**显示**（镜像）——拿它写记忆，就把你上次选的那一档擦了 ⇒ 用户 11-21
 *    「每次打开一个 Agent，都不会记住我上次的选择」。
 * 2. **记忆按 Agent 分**（用户 11-21：「每个 Agent 各记各的」）。
 * 3. **新建的 Agent 落在「上次那档」**（还没有自己那一档 ⇒ 用你上次选的）。
 */
const KEY = 'plobi.desktop.composer.approval-mode'
const AGENT_KEY_PREFIX = 'plobi.desktop.composer.approval-mode.agent.'
const AGENT_A = 'agent:butler'
const AGENT_B = 'agent:aura'

async function freshStore() {
  vi.resetModules()
  return await import('./session')
}

describe('approval mode 的记住与回落', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('没存过时默认「每次询问」', async () => {
    const { $approvalMode } = await freshStore()

    expect($approvalMode.get()).toBe('manual')
  })

  it('用户自己选的那一档落盘，重载后从那一档起步', async () => {
    const first = await freshStore()
    first.rememberApprovalMode(AGENT_A, 'off')

    expect(localStorage.getItem(KEY)).toBe('off')

    const second = await freshStore()
    expect(second.$approvalMode.get()).toBe('off')
  })

  it('网关报回来的那一档**不擦**记忆（它只是显示）', async () => {
    // 先让用户选过「完全访问」
    const first = await freshStore()
    first.rememberApprovalMode(AGENT_A, 'off')

    // 网关报回来的「生效档」＝会话自己的档 ‖ 全局兜底；它只改镜像。
    const second = await freshStore()
    second.setApprovalMode('manual')

    expect(second.$approvalMode.get()).toBe('manual')
    // 记忆没被动过 —— 打开这个 Agent 时还拿得回「完全访问」。
    expect(localStorage.getItem(KEY)).toBe('off')
    expect(second.rememberedApprovalMode(AGENT_A)).toBe('off')
  })

  it('每个 Agent 各记各的', async () => {
    const store = await freshStore()

    store.rememberApprovalMode(AGENT_A, 'off')
    store.rememberApprovalMode(AGENT_B, 'smart')

    expect(store.rememberedApprovalMode(AGENT_A)).toBe('off')
    expect(store.rememberedApprovalMode(AGENT_B)).toBe('smart')
    expect(store.approvalModeForAgent(AGENT_A)).toBe('off')
    expect(store.approvalModeForAgent(AGENT_B)).toBe('smart')
  })

  it('新建的 Agent（还没自己那一档）落在「上次那档」', async () => {
    const store = await freshStore()

    store.rememberApprovalMode(AGENT_A, 'off')

    // 另一个 Agent 从没设过 ⇒ 落在「上次那档」（off）。
    expect(store.rememberedApprovalMode(AGENT_B)).toBeUndefined()
    expect(store.approvalModeForAgent(AGENT_B)).toBe('off')
  })

  it('脏值不当档位用，退回默认', async () => {
    localStorage.setItem(KEY, 'yolo')
    const { $approvalMode } = await freshStore()

    expect($approvalMode.get()).toBe('manual')
  })
})
