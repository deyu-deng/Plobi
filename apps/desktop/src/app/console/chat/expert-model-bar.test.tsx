// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpertModelBar } from './expert-model-bar'

/**
 * 专家卡片底部那颗模型胶囊（用户 11-04）。
 *
 * 它挂在**角色**上而不是某一次会话上：选完存进 `experts/roles.json`，这个角色以后
 * 每次出场都用它；没配就显示「跟随 L2」（＝继承父 Agent，与普通子代理同一条默认）。
 */

const api = vi.fn()

const ROLES = {
  ok: true,
  data: {
    roles: [
      { duty: '你是软件与脚本专家', key: 'software', label: '软件', mark: '软', model: 'workbuddy/deepseek-v4.1-flash', toolsets: ['terminal', 'file'] },
      { duty: '你是文件与资料专家', key: 'file', label: '文件', mark: '文', model: null, toolsets: ['file'] }
    ]
  }
}

const OPTIONS = {
  providers: [{ models: ['deepseek-v4.1-flash'], name: 'WorkBuddy', slug: 'workbuddy' }]
}

beforeEach(() => {
  api.mockReset()
  api.mockImplementation((request: { path: string }) => {
    if (request.path.startsWith('/api/model/options')) {
      return Promise.resolve(OPTIONS)
    }

    return Promise.resolve(ROLES)
  })
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { ...(window.plobiDesktop || {}), api }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ExpertModelBar', () => {
  it('配过的角色显示它自己的模型名', async () => {
    render(<ExpertModelBar role="software" />)

    await waitFor(() => expect(screen.getByRole('button').textContent).toContain('deepseek-v4.1-flash'))
  })

  it('没配的角色不显示任何模型名——那一颗就是「跟随 L2」', async () => {
    render(<ExpertModelBar role="file" />)

    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(screen.getByRole('button').textContent).not.toContain('deepseek-v4.1-flash')
  })

  it('读不到角色配置时不炸，退回「跟随 L2」', async () => {
    api.mockRejectedValue(new Error('boom'))

    render(<ExpertModelBar role="software" />)

    await waitFor(() => expect(screen.getByRole('button').textContent).not.toContain('deepseek-v4.1-flash'))
  })
})
