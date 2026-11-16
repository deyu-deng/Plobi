// @vitest-environment jsdom
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { queryClient } from '@/lib/query-client'

import { ExpertsTab } from './experts-tab'

/**
 * 「名手」那一格（Capabilities 屏的第五个子 tab）。
 *
 * 钉四件事：
 * 1. 屏上写的是后端给的**人话标签**（"跑命令"），能力项键（`run_commands`）不上屏；
 * 2. 只画这一类**默认就有**的能力项——池子里它没有的那些（"看图"）不画开关
 *    （后端只认黑名单，画了也存不进去）；
 * 3. 关掉一项 ⇒ 写它自己的黑名单；后端不认得的能力项 ⇒ 那句原话要报到人眼前；
 * 4. 模型那一颗读这一格自己的名单：配过的显示模型名，没配的显示跟随首席。
 */

const notify = vi.hoisted(() => vi.fn())
const notifyError = vi.hoisted(() => vi.fn())

vi.mock('@/store/notifications', () => ({
  notify: (...args: unknown[]) => notify(...args),
  notifyError: (...args: unknown[]) => notifyError(...args)
}))

const api = vi.hoisted(() => vi.fn())

const POOL = [
  { key: 'read_files', label: '读文件' },
  { key: 'write_files', label: '写文件' },
  { key: 'run_commands', label: '跑命令' },
  { key: 'browse', label: '上网' },
  { key: 'read_pages', label: '抄网页' },
  { key: 'see_images', label: '看图' },
  { key: 'operate_desktop', label: '操作窗口' }
]

const FILE_ROLE = {
  capabilities: ['读文件', '写文件'],
  disabled_capabilities: [],
  duty: '你是名手（文件与资料）',
  effective_capabilities: ['读文件', '写文件'],
  effective_toolsets: ['file'],
  key: 'file',
  label: '文件',
  mark: '文',
  missing_capabilities: [],
  model: null,
  toolsets: ['file']
}

const SOFTWARE_ROLE = {
  capabilities: ['跑命令', '读文件', '写文件'],
  disabled_capabilities: [],
  duty: '你是名手（软件与脚本）',
  effective_capabilities: ['跑命令', '读文件', '写文件'],
  effective_toolsets: ['terminal', 'file'],
  key: 'software',
  label: '软件',
  mark: '软',
  missing_capabilities: [],
  model: 'workbuddy/deepseek-v4.1-flash',
  toolsets: ['terminal', 'file']
}

const ROLES_BODY = { data: { capabilities: POOL, roles: [FILE_ROLE, SOFTWARE_ROLE] }, ok: true }
const OPTIONS_BODY = { providers: [{ models: ['deepseek-v4.1-flash'], name: 'WorkBuddy', slug: 'workbuddy' }] }

beforeEach(() => {
  api.mockReset()
  api.mockImplementation((request: { method?: string; path: string }) => {
    if (request.path.startsWith('/api/model/options')) {
      return Promise.resolve(OPTIONS_BODY)
    }

    if (request.method === 'PUT') {
      return Promise.resolve({ data: { disabled_capabilities: [], model: null, role: 'software' }, ok: true })
    }

    return Promise.resolve(ROLES_BODY)
  })
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { ...(window.plobiDesktop || {}), api }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  // Shared singleton client — drop the cached roles so each test refetches.
  queryClient.clear()
})

function renderTab() {
  return render(
    <QueryClientProvider client={queryClient}>
      <ExpertsTab />
    </QueryClientProvider>
  )
}

describe('名手那一格', () => {
  it('每类名手一行，能力项写后端给的人话标签——能力项键不上屏', async () => {
    renderTab()

    await screen.findByRole('switch', { name: 'Toggle 跑命令 for 软件' })
    await screen.findByRole('switch', { name: 'Toggle 读文件 for 文件' })

    expect(screen.queryByText('run_commands')).toBeNull()
    expect(screen.queryByText('read_files')).toBeNull()
  })

  it('默认没有的能力项不画开关——后端只认黑名单，画了也存不进去', async () => {
    renderTab()

    await screen.findByRole('switch', { name: 'Toggle 跑命令 for 软件' })

    expect(screen.queryByRole('switch', { name: /看图/ })).toBeNull()
    expect(screen.queryByText('看图')).toBeNull()
  })

  it('关掉一项能力，写的是它自己的黑名单', async () => {
    renderTab()

    const toggle = await screen.findByRole('switch', { name: 'Toggle 跑命令 for 软件' })

    expect(toggle.getAttribute('aria-checked')).toBe('true')

    fireEvent.click(toggle)

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { disabled_capabilities: ['run_commands'] },
          method: 'PUT',
          path: '/api/experts/roles/software'
        })
      )
    )
  })

  it('后端不认得的能力项，那句原话要报到人眼前', async () => {
    api.mockImplementation((request: { method?: string; path: string }) => {
      if (request.path.startsWith('/api/model/options')) {
        return Promise.resolve(OPTIONS_BODY)
      }

      if (request.method === 'PUT') {
        return Promise.reject(new Error('400: {"ok":false,"error":"不认得的能力项：run_commands"}'))
      }

      return Promise.resolve(ROLES_BODY)
    })

    renderTab()

    const toggle = await screen.findByRole('switch', { name: 'Toggle 跑命令 for 软件' })

    fireEvent.click(toggle)

    await waitFor(() => expect(notifyError).toHaveBeenCalled())

    const [error] = notifyError.mock.calls[0] as [Error]

    expect(error.message).toContain('不认得的能力项：run_commands')
  })

  it('模型那一颗读这一格自己的名单：配过的显示模型名，没配的显示跟随首席', async () => {
    renderTab()

    expect(await screen.findByText('deepseek-v4.1-flash')).toBeTruthy()
    expect(screen.getByText('Follow the Principal')).toBeTruthy()
  })
})
