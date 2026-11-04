import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 权限档（每次询问 / 自动审批 / 完全访问）要"记住上一次"（用户 10-31 问的）。
 * 已有会话仍以网关报的为准；这里存的是**新对话草稿的默认档**——不存的话每次
 * 重启都弹回 manual，用户刚设好的那档就丢了。
 */
const KEY = 'plobi.desktop.composer.approval-mode'

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

  it('设过的档位落盘，重载后从那一档起步', async () => {
    const first = await freshStore()
    first.setApprovalMode('off')

    expect(localStorage.getItem(KEY)).toBe('off')

    const second = await freshStore()
    expect(second.$approvalMode.get()).toBe('off')
  })

  it('网关报回来的那一档覆盖记忆（镜像永远优先）', async () => {
    localStorage.setItem(KEY, 'smart')
    const { $approvalMode, setApprovalMode } = await freshStore()

    expect($approvalMode.get()).toBe('smart')

    // session.info / gateway-event 都用同一个 setter 落镜像
    setApprovalMode('manual')
    expect($approvalMode.get()).toBe('manual')
    expect(localStorage.getItem(KEY)).toBe('manual')
  })

  it('脏值不当档位用，退回默认', async () => {
    localStorage.setItem(KEY, 'yolo')
    const { $approvalMode } = await freshStore()

    expect($approvalMode.get()).toBe('manual')
  })
})
