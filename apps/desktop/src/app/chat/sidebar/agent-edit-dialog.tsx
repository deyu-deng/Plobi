import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { previewAgentRename, renameAgent, suggestAgentName, updateAgent } from '@/app/console/api'
import { refreshConsoleAgents } from '@/app/console/store/agents'
import type { Agent } from '@/app/console/types'
import { agentRoute, agentRouteId } from '@/app/routes'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { pickProjectFolder } from '@/store/projects'

import { AgentFormDialog } from './agent-form'
import { agentIdFromEnglish, isValidEnglishName } from './create-agent'

/**
 * 「三个点 → 编辑这个 Agent」（用户 10-21：「可以编辑更多信息，比如修改添加
 * 工作目录。重命名等等」）。
 *
 * 2026-11-16（i18n 定名刀）之后的规矩：这里只有**名字**，分两种语言写法，没有身份/门牌。
 *   - 中文名（`displayName`）：左栏那行字
 *   - 英文名（`displayNameEn`）：英文界面里那行字，同时是门牌 `agent_id` 的唯一来源
 * 门牌不让手填——用户想改门牌就改英文名，`agentIdFromEnglish` 派生出新的 `agent_id`，
 * 派活、记忆、历史跟着换（还是要走一遍 dry-run 清单再确认，见 `confirmRename`）。
 *   - 图标与颜色（创建时选过、左栏以前从不渲染，现在两边一致）
 *   - 工作目录清单（主文件夹在前；后端取首个驱动右栏挂载）
 *
 * 落库走 `PATCH /api/agents/:id` 窄口：只送用户真改过的格，`role`/`model`/
 * `provider` 这些不在这张表单上、也不会被顺手清掉。
 */
export function AgentEditDialog({
  agent,
  onOpenChange,
  open
}: {
  agent: Agent
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const { t } = useI18n()
  const c = t.console
  const navigate = useNavigate()
  const location = useLocation()
  const [displayName, setDisplayName] = useState(agent.name || agent.id)
  const [icon, setIcon] = useState(agent.icon ?? '')
  const [color, setColor] = useState(agent.color ?? '')
  const [paths, setPaths] = useState<string[]>(agent.projectPaths ?? (agent.projectPath ? [agent.projectPath] : []))
  const [busy, setBusy] = useState(false)
  // 英文名那一格：它既是英文界面里的那行字，也是门牌 `agent_id` 的来源，所以用户
  // 想改门牌就改这里。确认面板要的那份清单在点下「改名…」之后才向后端要（dry-run），
  // 拿到才显示——不预告一个可能失败的清单。
  const [englishName, setEnglishName] = useState(agent.displayNameEn ?? '')
  const [renameSteps, setRenameSteps] = useState<string[] | null>(null)
  // 保存时如果英文名改了，得连门牌一起搬。这一步不许「先把显示名落库、门牌回头再说」
  // （用户 2026-11-22 亲眼看见的裂缝：侧栏 Plobi-Desktop，对话框还在念旧 id）。
  // 所以清单摊出来之前一格都没写盘，确认之后 rename + patch 连着做完。
  const [pendingSave, setPendingSave] = useState<null | { name: string; nextId: string }>(null)
  const [renaming, setRenaming] = useState(false)
  const [translating, setTranslating] = useState(false)

  // 刷新按钮：翻得不对就重翻一次（用户 2026-11-22）。翻译源是中文名。
  async function retranslate() {
    const source = displayName.trim()

    if (!source || translating) {
      return
    }

    setTranslating(true)

    try {
      const result = await suggestAgentName(source, { refresh: true })

      if (result.englishName) {
        setEnglishName(result.englishName)
        setRenameSteps(null)
      }
    } catch {
      // 翻不出来就维持原样，让用户自己写。
    } finally {
      setTranslating(false)
    }
  }
  const nextId = agentIdFromEnglish(englishName)

  // 每次打开都从名册现状回填：攒着上一次关窗时的半截输入再来一次很吓人。
  useEffect(() => {
    if (!open) {
      return
    }

    setDisplayName(agent.name || agent.id)
    setEnglishName(agent.displayNameEn ?? '')
    setRenameSteps(null)
    setIcon(agent.icon ?? '')
    setColor(agent.color ?? '')
    setPaths(agent.projectPaths ?? (agent.projectPath ? [agent.projectPath] : []))
  }, [agent.color, agent.displayNameEn, agent.icon, agent.id, agent.name, agent.projectPath, agent.projectPaths, open])

  // 真正落库的那一下：改名（门牌 + 名册 + 磁盘 + session 全搬）先做，再用**新 id**
  // 把这张表单的字段写回去。顺序反了就会出现「显示名是新名字、门牌还是旧的」这种
  // 半截状态——那正是用户 11-22 看到的分裂。
  const confirmRename = async () => {
    const next = pendingSave?.nextId

    if (!next) {
      return
    }

    setRenaming(true)

    try {
      await renameAgent(agent.id, next)
      await patchFields(next, pendingSave.name)
      setRenameSteps(null)
      setPendingSave(null)
      await refreshConsoleAgents()

      // 门牌换了，路由也得跟着走：还停在 `/agents/<旧id>` 的话，输入框、名册
      // 查表、会话归属全都对着一个已经不存在的 id 取值。
      if ((agentRouteId(location.pathname) || '').toLowerCase() === agent.id.toLowerCase()) {
        navigate(agentRoute(next), { replace: true })
      }

      notify({ message: c.agentEditRenamed })
      onOpenChange(false)
    } catch (cause) {
      notifyError(cause, c.agentEditRenameFailed)
    } finally {
      setRenaming(false)
    }
  }

  // 只送用户真改过的格；一个目录都没绑的（butler）不送这一格，免得把「本来就没有」
  // 写成「清空」。
  const patchFields = async (id: string, name: string) => {
    await updateAgent(id, {
      color,
      displayName: name,
      // 英文名跟着这张表单一起存：它就是门牌来源，空了会让英文界面回落到中文名
      // （后端允许空串，语义是「没有英文名」）。
      displayNameEn: englishName.trim(),
      icon,
      ...(paths.length ? { projectPaths: paths } : {})
    })
  }

  const addFolder = async () => {
    const dir = await pickProjectFolder()

    if (dir && !paths.includes(dir)) {
      setPaths([...paths, dir])
    }
  }

  // 保存一键到底：英文名没改就只落字段；改了就把 dry-run 清单摊出来（此刻一格都
  // 没写盘），用户点确认才 rename + patch 连着做。以前这里先 patch 再问改名，等于
  // 把「名字改了但门牌没改」当成正常结果给了用户。
  const save = async () => {
    const name = displayName.trim()

    if (!name) {
      return
    }

    setBusy(true)

    try {
      const next = isValidEnglishName(englishName) ? agentIdFromEnglish(englishName) : ''
      const needsRename = Boolean(next) && next.toLowerCase() !== agent.id.toLowerCase()

      if (!needsRename) {
        await patchFields(agent.id, name)
        await refreshConsoleAgents()
        notify({ message: c.agentEditSaved })
        onOpenChange(false)

        return
      }

      const preview = await previewAgentRename(agent.id, next)

      setPendingSave({ name, nextId: next })
      setRenameSteps(preview.steps ?? [])
    } catch (cause) {
      notifyError(cause, c.agentEditFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AgentFormDialog
      appearanceLabels={{ clear: c.agentEditAppearanceClear, label: c.agentEditAppearanceLabel }}
      busy={busy}
      busyLabel={c.agentEditSaving}
      canSubmit={Boolean(displayName.trim())}
      cancelLabel={c.agentEditCancel}
      color={color}
      description={c.agentEditBody.replace('{name}', agent.id)}
      displayName={displayName}
      englishHelp={c.newAgentEnglishHelp}
      englishLabel={c.newAgentEnglishLabel}
      englishName={englishName}
      extra={
        renameSteps && (
          <div
            className="flex flex-col gap-1.5 rounded-md border border-(--ui-stroke-secondary) p-2"
            role="alertdialog"
          >
            <span className="text-xs font-medium">
              {c.agentEditRenameTitle.replace('{name}', pendingSave?.nextId ?? nextId)}
            </span>
            <span className="text-[0.6875rem] text-(--ui-text-tertiary)">
              {c.agentEditRenameBody.replace('{count}', String(renameSteps.length))}
            </span>
            <ul className="flex max-h-32 flex-col gap-0.5 overflow-y-auto text-[0.6875rem] text-(--ui-text-quaternary)">
              {renameSteps.map(line => (
                <li className="truncate" key={line} title={line}>
                  {line}
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-1.5">
              <Button
                disabled={renaming}
                onClick={() => {
                  void confirmRename()
                }}
                size="sm"
                type="button"
              >
                {c.agentEditRenameConfirm}
              </Button>
              <Button
                disabled={renaming}
                onClick={() => {
                  setRenameSteps(null)
                  setPendingSave(null)
                }}
                size="sm"
                type="button"
                variant="ghost"
              >
                {c.agentEditRenameCancel}
              </Button>
            </div>
          </div>
        )
      }
      folderLabels={{
        add: c.agentEditPathAdd,
        empty: c.agentEditPathsEmpty,
        label: c.agentEditPathsLabel,
        primary: c.agentEditPathPrimary,
        remove: c.agentEditPathRemove
      }}
      icon={icon}
      idPrefix="edit-agent"
      nameLabel={c.agentEditNameLabel}
      onAddFolder={() => {
        void addFolder()
      }}
      onCancel={() => onOpenChange(false)}
      onChangeColor={setColor}
      onChangeIcon={setIcon}
      onClearAppearance={() => {
        setIcon('')
        setColor('')
      }}
      onDisplayNameChange={setDisplayName}
      onEnglishNameChange={value => {
        setEnglishName(value)
        setRenameSteps(null)
      }}
      onOpenChange={onOpenChange}
      onRemoveFolder={path => setPaths(paths.filter(item => item !== path))}
      onRetranslate={() => {
        void retranslate()
      }}
      onSubmit={() => {
        void save()
      }}
      open={open}
      paths={paths}
      preview={
        <div className="flex items-center gap-1.5 text-[0.6875rem] text-(--ui-text-tertiary)">
          <span>{c.agentEditPreviewLabel}</span>
          {icon
            ? (
                <span className="flex items-center gap-1" style={color ? { color } : undefined}>
                  <Codicon name={icon} size="0.875rem" />
                  {displayName.trim() || agent.id}
                </span>
              )
            : (
                <span className="flex items-center gap-1">
                  <span className="size-2 rounded-full" style={{ backgroundColor: color || 'var(--ui-text-quaternary)' }} />
                  {displayName.trim() || agent.id}
                </span>
              )}
        </div>
      }
      retranslateLabel={c.retranslateLabel}
      submitLabel={c.agentEditSave}
      title={c.agentEditTitle}
      translating={translating}
    />
  )
}
