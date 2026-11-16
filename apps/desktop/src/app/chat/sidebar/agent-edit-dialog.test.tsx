import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Agent } from '@/app/console/types'

import { AgentEditDialog } from './agent-edit-dialog'

// 「编辑这个 Agent」里那两格名字是同一个名字的两种语言写法（2026-11-16 i18n 定名刀）：
//   · 中文名＝左栏那行字，改了只有左栏跟着变；
//   · 英文名＝英文界面那行字，同时是门牌 `agent_id` 的唯一来源。改英文名＝改门牌，
//     会话历史、记忆、日程、看板上的卡、Mind 那棵子树都跟着走。
// 所以第二格必须：① 只收英文名，门牌由它派生、摊在眼前给用户看；② 点下去先要一份
// 「会动哪几处」的清单（dry-run）；③ 清单看过、确认之后才真改；④ 没动它、或者英文名
// 不合法时那颗按钮根本不出现。

const updateAgent = vi.fn(async () => ({ id: 'aura' }) as never)
const previewAgentRename = vi.fn(
  async () =>
    ({ new: 'aura-new', old: 'aura', steps: ['名册那一行：aura → aura-new', '搬档案文件：souls/aura.md → souls/aura-new.md'] }) as never
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
        agentEditTitle: '编辑 Agent',
        newAgentEnglishHelp: '它会转成 Agent 的机器键（小写、连字符）；左栏显示的还是中文名。',
        newAgentEnglishLabel: '英文名'
      }
    }
  })
}))

const agent: Agent = {
  category: 'projects',
  displayNameEn: 'Aura',
  id: 'aura',
  name: '光环',
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

function typeEnglish(value: string) {
  fireEvent.change(screen.getByLabelText('英文名'), { target: { value } })
}

describe('AgentEditDialog — 中文名与英文名是两格', () => {
  it('shows both name fields, and the English one is prefilled with the stored English name', () => {
    renderDialog()

    expect(screen.getByText('英文名')).toBeTruthy()
    // 中文名那一格是「光环」，英文名那一格是 Aura——两格不是一格
    expect(screen.getByDisplayValue('光环')).toBeTruthy()
    expect(screen.getByDisplayValue('Aura')).toBeTruthy()
  })

  // 门牌不是另一样东西：英文名一改，派生出来的 agent id 立刻摊在输入框下面，
  // 用户在这张表单上永远看不到「请输入 id」这种要求。
  it('spells out the agent id it derives from the English name', () => {
    renderDialog()

    expect(screen.getByText('aura')).toBeTruthy()

    typeEnglish('Aura Nova')

    expect(screen.getByText('aura-nova')).toBeTruthy()
  })

  it('offers no rename button while the English name is empty or not ASCII', () => {
    renderDialog()

    typeEnglish('')
    expect(screen.queryByText('改名…')).toBeNull()

    typeEnglish('光环二号')
    expect(screen.queryByText('改名…')).toBeNull()
  })

  // 保存那一格是 PATCH 窄口：文件名（displayNameEn）与中文名一起送，门牌不在其内。
  it('saves the English name through the narrow PATCH', async () => {
    renderDialog()

    typeEnglish('Aura Nova')
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() =>
      expect(updateAgent).toHaveBeenCalledWith(
        'aura',
        expect.objectContaining({ displayName: '光环', displayNameEn: 'Aura Nova' })
      )
    )
    expect(renameAgent).not.toHaveBeenCalled()
  })

  it('offers no rename button until the identity field actually changes', () => {
    renderDialog()

    expect(screen.queryByText('改名…')).toBeNull()

    typeEnglish('aura-new')

    expect(screen.getByText('改名…')).toBeTruthy()
  })

  it('asks for the plan first, and does not touch anything yet', async () => {
    renderDialog()
    typeEnglish('aura-new')

    fireEvent.click(screen.getByText('改名…'))

    await waitFor(() => expect(previewAgentRename).toHaveBeenCalledWith('aura', 'aura-new'))
    expect(renameAgent).not.toHaveBeenCalled()
    expect(screen.getByText('改成「aura-new」？')).toBeTruthy()
    expect(screen.getByText('会一起改过来的地方（2 处）：')).toBeTruthy()
    expect(screen.getByText('名册那一行：aura → aura-new')).toBeTruthy()
  })

  it('renames only on the confirm, then refreshes the rail and closes', async () => {
    renderDialog()
    typeEnglish('aura-new')
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
    typeEnglish('aura-new')
    fireEvent.click(screen.getByText('改名…'))

    await waitFor(() => expect(screen.getByText('先不改')).toBeTruthy())
    fireEvent.click(screen.getByText('先不改'))

    expect(renameAgent).not.toHaveBeenCalled()
    expect(screen.queryByText('改成「aura-new」？')).toBeNull()
  })

  it('says so out loud when the plan cannot be fetched', async () => {
    previewAgentRename.mockRejectedValueOnce(new Error('后端没答上'))
    renderDialog()
    typeEnglish('aura-new')

    fireEvent.click(screen.getByText('改名…'))

    await waitFor(() => expect(notifyError).toHaveBeenCalled())
    expect(renameAgent).not.toHaveBeenCalled()
  })
})
