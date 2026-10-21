import { useEffect, useState } from 'react'

import { updateAgent } from '@/app/console/api'
import { refreshConsoleAgents } from '@/app/console/store/agents'
import type { Agent } from '@/app/console/types'
import { Codicon } from '@/components/ui/codicon'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { pickProjectFolder } from '@/store/projects'

import { AGENT_COLOR_CHOICES, AGENT_ICON_CHOICES } from './create-agent'

/**
 * 「三个点 → 编辑这个 Agent」（用户 10-21：「可以编辑更多信息，比如修改添加
 * 工作目录。重命名等等」）。
 *
 * 能改的就是**人和这个 Agent 的关系**，不是它的身份：
 *   - 显示名（左栏那行字；`id` / profile 家不动——改了会把会话历史孤儿化）
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
  const [displayName, setDisplayName] = useState(agent.name || agent.id)
  const [icon, setIcon] = useState(agent.icon ?? '')
  const [color, setColor] = useState(agent.color ?? '')
  const [paths, setPaths] = useState<string[]>(agent.projectPaths ?? (agent.projectPath ? [agent.projectPath] : []))
  const [busy, setBusy] = useState(false)

  // 每次打开都从名册现状回填：攒着上一次关窗时的半截输入再来一次很吓人。
  useEffect(() => {
    if (!open) {
      return
    }

    setDisplayName(agent.name || agent.id)
    setIcon(agent.icon ?? '')
    setColor(agent.color ?? '')
    setPaths(agent.projectPaths ?? (agent.projectPath ? [agent.projectPath] : []))
  }, [agent.color, agent.icon, agent.id, agent.name, agent.projectPath, agent.projectPaths, open])

  const addFolder = async () => {
    const dir = await pickProjectFolder()

    if (dir && !paths.includes(dir)) {
      setPaths([...paths, dir])
    }
  }

  const save = async () => {
    const name = displayName.trim()

    if (!name) {
      return
    }

    setBusy(true)

    try {
      await updateAgent(agent.id, {
        color,
        displayName: name,
        icon,
        // 只有这张表单上真能改的目录才送；一个都没绑的（butler）不送这一格，
        // 免得把「本来就没有」写成「清空」。
        ...(paths.length ? { projectPaths: paths } : {})
      })
      await refreshConsoleAgents()
      notify({ message: c.agentEditSaved })
      onOpenChange(false)
    } catch (cause) {
      notifyError(cause, c.agentEditFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{c.agentEditTitle}</DialogTitle>
          <DialogDescription>{c.agentEditBody.replace('{name}', agent.name || agent.id)}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">{c.agentEditNameLabel}</span>
            <input
              className="h-8 w-full rounded-md border border-(--ui-stroke-tertiary) bg-transparent px-2 text-sm outline-none focus:border-(--ui-stroke-secondary)"
              onChange={event => setDisplayName(event.target.value)}
              value={displayName}
            />
          </label>

          <div className="flex flex-col gap-1.5">
            <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">{c.agentEditAppearanceLabel}</span>
            <div className="flex flex-wrap items-center gap-1">
              {AGENT_ICON_CHOICES.map(name => (
                <button
                  aria-label={name}
                  aria-pressed={icon === name}
                  className={cn(
                    'flex size-7 items-center justify-center rounded-md',
                    icon === name
                      ? 'bg-(--ui-control-hover-background) text-foreground'
                      : 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground'
                  )}
                  key={name}
                  onClick={() => setIcon(icon === name ? '' : name)}
                  type="button"
                >
                  <Codicon name={name} size="0.875rem" />
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {AGENT_COLOR_CHOICES.map(choice => (
                <button
                  aria-label={choice}
                  aria-pressed={color === choice}
                  className={cn(
                    'size-5 rounded-full border transition-transform hover:scale-110',
                    color === choice ? 'border-foreground' : 'border-transparent'
                  )}
                  key={choice}
                  onClick={() => setColor(color === choice ? '' : choice)}
                  style={{ backgroundColor: choice }}
                  type="button"
                />
              ))}
              <button
                aria-label={c.agentEditAppearanceClear}
                className="flex size-5 items-center justify-center rounded-full text-(--ui-text-quaternary) hover:text-foreground"
                onClick={() => {
                  setIcon('')
                  setColor('')
                }}
                type="button"
              >
                <Codicon name="close" size="0.75rem" />
              </button>
            </div>
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
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">{c.agentEditPathsLabel}</span>
            {paths.length === 0 && (
              <p className="text-[0.6875rem] text-(--ui-text-quaternary)">{c.agentEditPathsEmpty}</p>
            )}
            <ul className="flex flex-col gap-1">
              {paths.map((path, index) => (
                <li
                  className="flex items-center gap-1.5 rounded-md border border-(--ui-stroke-tertiary) px-2 py-1 text-xs"
                  key={path}
                >
                  <Codicon className="shrink-0 text-(--ui-text-tertiary)" name={index === 0 ? 'folder-active' : 'folder'} size="0.75rem" />
                  <span className="min-w-0 flex-1 truncate" title={path}>
                    {path}
                  </span>
                  {index === 0 && (
                    <span className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)">{c.agentEditPathPrimary}</span>
                  )}
                  <button
                    aria-label={c.agentEditPathRemove}
                    className="flex size-5 shrink-0 items-center justify-center rounded text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground"
                    onClick={() => setPaths(paths.filter(item => item !== path))}
                    type="button"
                  >
                    <Codicon name="close" size="0.75rem" />
                  </button>
                </li>
              ))}
            </ul>
            <button
              aria-label={c.agentEditPathAdd}
              className="flex items-center gap-1 self-start rounded-md px-1.5 py-0.5 text-[0.6875rem] text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background) hover:text-foreground"
              onClick={() => {
                void addFolder()
              }}
              type="button"
            >
              <Codicon name="add" size="0.75rem" />
              {c.agentEditPathAdd}
            </button>
          </div>
        </div>

        <DialogFooter>
          <button
            className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-(--ui-control-hover-background)"
            disabled={busy}
            onClick={() => onOpenChange(false)}
            type="button"
          >
            {c.agentEditCancel}
          </button>
          <button
            className="rounded-md bg-(--ui-accent) px-3 py-1.5 text-sm text-(--ui-accent-foreground) disabled:opacity-60"
            disabled={busy || !displayName.trim()}
            onClick={() => {
              void save()
            }}
            type="button"
          >
            {busy ? c.agentEditSaving : c.agentEditSave}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
