import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Agent } from '@/app/console/types'

import { AgentEditDialog } from './agent-edit-dialog'

// 「编辑这个 Agent」里那两格名字是两件事（WP-AGENT-RENAME-FULL-CHAIN B 段）：
//   · 显示名＝你和它的关系，改了只有左栏跟着变；
//   · 身份名＝它本人，改了会话历史、记忆、日程、看板上的卡、Mind 那棵子树都跟着走。
// 所以第二格必须：① 与显示名分开；② 点下去先要一份「会动哪几处」的清单（dry-run）；
// ③ 清单看过、确认之后才真改；④ 没动它时那颗按钮根本不出现。

const updateAgent = vi.fn(async () => ({ id: 'aura' }) as never)
const previewAgentRename = vi.fn(
  async () =>
    ({ new: 'aura-new', old: 'aura', steps: ['名册那一行：aura → aura-new', '搬身份文件：souls/aura.md → souls/aura-new.md'] }) as never
)
const renameAgent = vi.fn(async () => ({ id: 'aura-new', previousId: 'aura' }) as never)
const refreshConsoleAgents = vi.fn(async () => {})
const pickProjectFolder = vi.fn(async (): Promise<null | string> => null)
const notify = vi.fn()
const notifyError = vi.fn()
const onOpenChange = vi.fn()

vi.mock('@/app/console/api', () => ({
  previewAgentRename: (...args: unknown[]) => previewAgentRename(...(args as [])),
  renameAgent: (...args: unknown[]) => renameAgent(...(args as [])),
  updateAgent: (...args: unknown[]) => updateAgent(...(args as []))
}))

vi.mock('@/store/projects', () => ({
  pickProjectFolder: () => pickProjectFolder()
}))

vi.mock('@/app/console/store/agents', () => ({
  refreshConsoleAgents: () => refreshConsoleAgents()
}))

vi.mock('@/store/notifications', () => ({
  notify: (input: unknown) => notify(input),
  notifyError: (error: unknown, fallback: string) => notifyError(error, fallback)
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { close: '关闭' },
      console: {
        agentEditAppearanceClear: '清掉图标和颜色',
        agentEditAppearanceLabel: '图标与颜色',
        agentEditBody: '改的是 {name} 的名字、样子和文件夹。',
        agentEditCancel: '取消',
        agentEditFailed: '这个 Agent 没能更新：',
        agentEditIdentityChange: '改名…',
        agentEditIdentityLabel: '这个名字',
        agentEditIdentityNote: '派活、记忆、历史都按它认人',
        agentEditNameLabel: '左栏里叫它什么',
        agentEditPathAdd: '添加文件夹',
        agentEditPathPrimary: '主目录',
        agentEditPathRemove: '移除这个目录',
        agentEditPathsEmpty: '还没绑文件夹。',
        agentEditPathsLabel: '工作目录',
        agentEditPreviewLabel: '左栏效果',
        agentEditRenameBody: '会一起改过来的地方（{count} 处）：',
        agentEditRenameCancel: '先不改',
        agentEditRenameConfirm: '确认改名',
        agentEditRenameFailed: '改名失败：',
        agentEditRenamed: '已改名。',
        agentEditRenameTitle: '改成「{name}」？',
        agentEditSave: '保存',
        agentEditSaved: 'Agent 已更新。',
        agentEditSaving: '正在保存…',
        agentEditTitle: '编辑 Agent'
      }
    }
  })
}))

const agent: Agent = {
  category: 'projects',
  id: 'aura',
  name: 'Aura',
  profile: 'l2-aura',
  status: 'idle'
} as Agent

afterEach(() => {
  cleanup()
  updateAgent.mockClear()
  previewAgentRename.mockClear()
  renameAgent.mockClear()
  refreshConsoleAgents.mockClear()
  notify.mockClear()
  notifyError.mockClear()
  onOpenChange.mockClear()
})

function renderDialog() {
  render(<AgentEditDialog agent={agent} onOpenChange={onOpenChange} open />)
}

function typeIdentity(value: string) {
  fireEvent.change(screen.getByDisplayValue('aura'), { target: { value } })
}

describe('AgentEditDialog — 显示名与身份名是两格', () => {
  it('shows both name fields, and the identity one is prefilled with the real id', () => {
    renderDialog()

    expect(screen.getByText('这个名字')).toBeTruthy()
    expect(screen.getByText('派活、记忆、历史都按它认人')).toBeTruthy()
    // 显示名那一格是 Aura，身份那一格是 aura——两格不是一格
    expect(screen.getByDisplayValue('Aura')).toBeTruthy()
    expect(screen.getByDisplayValue('aura')).toBeTruthy()
  })

  it('offers no rename button until the identity field actually changes', () => {
    renderDialog()

    expect(screen.queryByText('改名…')).toBeNull()

    typeIdentity('aura-new')

    expect(screen.getByText('改名…')).toBeTruthy()
  })

  it('asks for the plan first, and does not touch anything yet', async () => {
    renderDialog()
    typeIdentity('aura-new')

    fireEvent.click(screen.getByText('改名…'))

    await waitFor(() => expect(previewAgentRename).toHaveBeenCalledWith('aura', 'aura-new'))
    expect(renameAgent).not.toHaveBeenCalled()
    expect(screen.getByText('改成「aura-new」？')).toBeTruthy()
    expect(screen.getByText('会一起改过来的地方（2 处）：')).toBeTruthy()
    expect(screen.getByText('名册那一行：aura → aura-new')).toBeTruthy()
  })

  it('renames only on the confirm, then refreshes the rail and closes', async () => {
    renderDialog()
    typeIdentity('aura-new')
    fireEvent.click(screen.getByText('改名…'))

    await waitFor(() => expect(screen.getByText('确认改名')).toBeTruthy())
    fireEvent.click(screen.getByText('确认改名'))

    await waitFor(() => expect(renameAgent).toHaveBeenCalledWith('aura', 'aura-new'))
    expect(refreshConsoleAgents).toHaveBeenCalled()
    expect(notify).toHaveBeenCalledWith({ message: '已改名。' })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('backs out of the confirm without renaming', async () => {
    renderDialog()
    typeIdentity('aura-new')
    fireEvent.click(screen.getByText('改名…'))

    await waitFor(() => expect(screen.getByText('先不改')).toBeTruthy())
    fireEvent.click(screen.getByText('先不改'))

    expect(renameAgent).not.toHaveBeenCalled()
    expect(screen.queryByText('改成「aura-new」？')).toBeNull()
  })

  it('says so out loud when the plan cannot be fetched', async () => {
    previewAgentRename.mockRejectedValueOnce(new Error('后端没答上'))
    renderDialog()
    typeIdentity('aura-new')

    fireEvent.click(screen.getByText('改名…'))

    await waitFor(() => expect(notifyError).toHaveBeenCalled())
    expect(renameAgent).not.toHaveBeenCalled()
  })
})
