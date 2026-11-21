// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { $gateway } from '@/store/gateway'
import { setActiveSessionId, setApprovalMode } from '@/store/session'

import { PermissionPill } from './permission-pill'
import { agentScopeKey } from './queue-key'

/**
 * 输入框左下角那枚权限档（用户 11-21 报障：「不管跳到那个状态都好像没有区别，
 * 而且每次打开一个 Agent，都不会记住我上次的选择」）。
 *
 * 这一档钉的是**接线**：
 * ① 用户自己选一档 ⇒ **写进记忆**（这个 Agent 一份 ＋ 「上次那档」一份）＋ 推给会话；
 * ② 打开一个 Agent ⇒ 把**这个 Agent 记住的那一档**推给那条会话（新建的 Agent 没有
 *    自己那一档 ⇒ 落在你上次选的那一档）。
 *
 * Radix 的下拉在 jsdom 里不吃合成指针事件（它靠 elementFromPoint）⇒ 这一档把菜单
 * 组件替成普通按钮，测的是**药丸自己的逻辑**（选中 ⇒ 写记忆 ＋ 推会话）。
 */
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: ReactNode }) => <div role="menu">{children}</div>,
  DropdownMenuItem: ({ children, onSelect }: { children: ReactNode; onSelect?: () => void }) => (
    <button onClick={() => onSelect?.()} role="menuitem" type="button">
      {children}
    </button>
  )
}))

const AGENT_KEY = agentScopeKey('/agent/butler')
const AGENT_MEMORY_KEY = `plobi.desktop.composer.approval-mode.agent.${encodeURIComponent(AGENT_KEY)}`
const LAST_MODE_KEY = 'plobi.desktop.composer.approval-mode'

// 真网关 config.set 会把设进去的那一档原样报回来（11-21 现核）⇒ 桩也回显，
// 否则固定回 'off' 会把药丸顶成「完全访问」，后面再点同一档就被 `next === mode` 早退。
const request = vi.fn(async (_method: string, params?: { mode?: string }) => ({
  mode: params?.mode ?? 'manual',
  value: '1'
}))

function renderPill() {
  return render(
    <I18nProvider configClient={{ getConfig: async () => ({}), saveConfig: async () => ({ ok: true }) }}>
      <MemoryRouter initialEntries={['/agent/butler']}>
        <PermissionPill />
      </MemoryRouter>
    </I18nProvider>
  )
}

describe('permission pill 的档位记忆', () => {
  beforeEach(() => {
    localStorage.clear()
    request.mockClear()
    // 药丸的显示档是模块级 atom：每个用例都从「每次询问」起步，不吃上一个用例的余温。
    setApprovalMode('manual')
    $gateway.set({ request } as unknown as NonNullable<ReturnType<typeof $gateway.get>>)
    setActiveSessionId('live-session-1')
  })

  afterEach(() => {
    cleanup()
    setActiveSessionId(null)
  })

  it('打开一个 Agent ⇒ 把这个 Agent 记住的那一档推给那条会话', async () => {
    // 用户之前在 butler 这个 Agent 上选过「完全访问」
    localStorage.setItem(AGENT_MEMORY_KEY, 'off')

    renderPill()

    await waitFor(() => expect(request).toHaveBeenCalled())
    expect(request).toHaveBeenCalledWith(
      'config.set',
      expect.objectContaining({ key: 'yolo', mode: 'off', session_id: 'live-session-1' }),
      undefined,
      undefined
    )
  })

  it('新建的 Agent（还没自己那一档）⇒ 落在「上次那档」', async () => {
    localStorage.setItem(LAST_MODE_KEY, 'smart')

    renderPill()

    await waitFor(() => expect(request).toHaveBeenCalled())
    expect(request).toHaveBeenCalledWith(
      'config.set',
      expect.objectContaining({ key: 'yolo', mode: 'smart' }),
      undefined,
      undefined
    )
  })

  it('用户自己选一档 ⇒ 写进这个 Agent 的记忆 ＋「上次那档」', async () => {
    renderPill()

    fireEvent.click(screen.getByRole('menuitem', { name: /full access/i }))

    await waitFor(() => expect(localStorage.getItem(LAST_MODE_KEY)).toBe('off'))
    expect(localStorage.getItem(AGENT_MEMORY_KEY)).toBe('off')
  })
})
