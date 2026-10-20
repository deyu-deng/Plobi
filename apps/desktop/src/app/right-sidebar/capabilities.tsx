import { useStore } from '@nanostores/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'

import { putAgentToolPrefs } from '@/app/console/api'
import { $consoleAgents, refreshConsoleAgents } from '@/app/console/store/agents'
import { AgentModelSection } from '@/app/right-sidebar/agent-model-section'
import { SidebarPanelLabel } from '@/app/shell/sidebar-label'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import {
  getGlobalMcpServers,
  getSkillsForProfile,
  getToolsetsForProfile,
  toggleSkillForProfile,
  toggleToolsetForProfile
} from '@/plobi'
import { notifyError } from '@/store/notifications'

/**
 * Agent Capabilities rail（全局真源 + per-agent 开关，2026-10-19 用户裁定）。
 *
 * 资产只有一张全局表：技能 / 工具集 / MCP 服务器装一个全员拥有。本面板挂在
 * L2（某个 Agent 的界面）里，展示的是「这个 Agent 关掉了哪些」——开关读写
 * 名册行的三份黑名单，全局表一个字不动：
 *   - 技能 / 工具集：`/api/skills*`、`/api/tools/toolsets*` 显式带
 *     `profile=<agent 家名>`（后端按名册行分叉，见 web_server `_agent_tool_row`）；
 *   - MCP：读全局 `mcp_servers` 名单 + 行内 `disabledMcpServers`，写走
 *     `PUT /api/agents/:id/tool-prefs`。
 *
 * 名单是全局真源的**减法视图**：全局关掉的东西这里也关（且标出来），这个
 * Agent 只能再关自己那份，不能替全局开。
 */

const SKILLS_KEY = (profile: string) => ['agent-capabilities', 'skills', profile] as const
const TOOLSETS_KEY = (profile: string) => ['agent-capabilities', 'toolsets', profile] as const
const MCP_KEY = ['agent-capabilities', 'mcp-servers'] as const

const ROW =
  'flex h-8 items-center gap-2 rounded-md px-2 text-[0.78rem] text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background)'

interface CapabilityRowProps {
  checked: boolean
  disabled?: boolean
  globalOff?: boolean
  label: string
  meta?: string
  onToggle: (enabled: boolean) => void
}

function CapabilityRow({ checked, disabled, globalOff, label, meta, onToggle }: CapabilityRowProps) {
  const { t } = useI18n()
  const r = t.rightSidebar

  return (
    <div className={cn(ROW, 'group/row justify-between')}>
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate">{label}</span>
        {meta && <span className="truncate text-[0.66rem] text-(--ui-text-quaternary)">{meta}</span>}
        {globalOff && <span className="truncate text-[0.66rem] text-(--ui-text-quaternary)">{r.capabilitiesGlobalOff}</span>}
      </div>
      <Switch
        aria-label={label}
        checked={checked}
        disabled={disabled || globalOff}
        onCheckedChange={onToggle}
        size="xs"
      />
    </div>
  )
}

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="flex shrink-0 items-center pb-1 pt-2">
      <SidebarPanelLabel>{label}</SidebarPanelLabel>
    </div>
  )
}

export function AgentCapabilitiesPane({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const r = t.rightSidebar
  const queryClient = useQueryClient()
  const agents = useStore($consoleAgents)
  const agent = useMemo(() => agents.find(row => row.id === agentId), [agentId, agents])

  const [busyKeys, setBusyKeys] = useState<ReadonlySet<string>>(new Set())

  const markBusy = useCallback((key: string, busy: boolean) => {
    setBusyKeys(current => {
      const next = new Set(current)

      if (busy) {
        next.add(key)
      } else {
        next.delete(key)
      }

      return next
    })
  }, [])

  const profile = agent?.profile ?? ''

  const skillsQuery = useQuery({
    enabled: Boolean(profile),
    queryFn: () => getSkillsForProfile(profile),
    queryKey: SKILLS_KEY(profile),
    staleTime: 0
  })

  const toolsetsQuery = useQuery({
    enabled: Boolean(profile),
    queryFn: () => getToolsetsForProfile(profile),
    queryKey: TOOLSETS_KEY(profile),
    staleTime: 0
  })

  const mcpQuery = useQuery({ queryFn: getGlobalMcpServers, queryKey: MCP_KEY, staleTime: 0 })

  const refreshRoster = useCallback(() => {
    void refreshConsoleAgents()
  }, [])

  const toggleSkill = useCallback(
    async (name: string, enabled: boolean) => {
      if (!profile || !agent) {
        return
      }

      markBusy(`skill:${name}`, true)

      try {
        await toggleSkillForProfile(profile, name, enabled)
        await queryClient.invalidateQueries({ queryKey: SKILLS_KEY(profile) })
        refreshRoster()
      } catch (cause) {
        notifyError(cause, r.capabilitiesToggleFailed)
      } finally {
        markBusy(`skill:${name}`, false)
      }
    },
    [agent, markBusy, profile, queryClient, r.capabilitiesToggleFailed, refreshRoster]
  )

  const toggleToolset = useCallback(
    async (name: string, enabled: boolean) => {
      if (!profile || !agent) {
        return
      }

      markBusy(`toolset:${name}`, true)

      try {
        await toggleToolsetForProfile(profile, name, enabled)
        await queryClient.invalidateQueries({ queryKey: TOOLSETS_KEY(profile) })
        refreshRoster()
      } catch (cause) {
        notifyError(cause, r.capabilitiesToggleFailed)
      } finally {
        markBusy(`toolset:${name}`, false)
      }
    },
    [agent, markBusy, profile, queryClient, r.capabilitiesToggleFailed, refreshRoster]
  )

  // MCP 的 per-agent 开关走 tool-prefs：把三份黑名单里的 disabledMcpServers
  // 全量回写（端点是 PUT 语义，传整份名单）。
  const toggleMcp = useCallback(
    async (name: string, enabled: boolean) => {
      if (!agent) {
        return
      }

      const current = new Set(agent.disabledMcpServers ?? [])
      const matched = [...current].find(key => key.toLowerCase() === name.toLowerCase())

      if (enabled) {
        if (matched) {
          current.delete(matched)
        }
      } else {
        current.add(matched ?? name)
      }

      markBusy(`mcp:${name}`, true)

      try {
        await putAgentToolPrefs(agent.id, { disabledMcpServers: [...current] })
        refreshRoster()
      } catch (cause) {
        notifyError(cause, r.capabilitiesToggleFailed)
      } finally {
        markBusy(`mcp:${name}`, false)
      }
    },
    [agent, markBusy, r.capabilitiesToggleFailed, refreshRoster]
  )

  if (!agent || !profile) {
    return <PaneEmpty label={r.capabilitiesEmpty} />
  }

  const agentMcpOff = new Set((agent.disabledMcpServers ?? []).map(name => name.toLowerCase()))
  const mcpNames = Object.keys(mcpQuery.data ?? {})

  return (
    <div className="flex min-h-0 flex-1 flex-col px-1.5 pb-2">
      <div className="flex h-7 shrink-0 items-center gap-1.5">
        <SidebarPanelLabel>{r.capabilitiesTitle}</SidebarPanelLabel>
        <span className="truncate text-[0.66rem] text-(--ui-text-quaternary)">{agent.name}</span>
      </div>
      <p className="shrink-0 px-2 pb-1 text-[0.66rem] leading-snug text-(--ui-text-quaternary)">
        {r.capabilitiesHint}
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain">
        <SectionHeader label={r.capabilitiesModel} />
        {/* 这一个 Agent 的模型：写名册那一行的覆盖，空＝跟随默认（WP-AGENT-MODEL-PICKER-MOUNT）。 */}
        <AgentModelSection agent={agent} />
        <SectionHeader label={r.capabilitiesSkills} />
        {skillsQuery.isError ? <LoadError onRetry={() => void skillsQuery.refetch()} /> : null}
        {skillsQuery.data?.length ? (
          skillsQuery.data.map(skill => (
            <CapabilityRow
              checked={skill.enabled}
              disabled={busyKeys.has(`skill:${skill.name}`)}
              key={skill.name}
              label={skill.name}
              meta={skill.description}
              onToggle={enabled => void toggleSkill(skill.name, enabled)}
            />
          ))
        ) : null}

        <SectionHeader label={r.capabilitiesToolsets} />
        {toolsetsQuery.isError ? <LoadError onRetry={() => void toolsetsQuery.refetch()} /> : null}
        {toolsetsQuery.data?.length ? (
          toolsetsQuery.data.map(toolset => (
            <CapabilityRow
              checked={toolset.enabled}
              disabled={busyKeys.has(`toolset:${toolset.name}`)}
              key={toolset.name}
              label={toolset.label}
              meta={toolset.description}
              onToggle={enabled => void toggleToolset(toolset.name, enabled)}
            />
          ))
        ) : null}

        <SectionHeader label={r.capabilitiesMcp} />
        {mcpQuery.isError ? <LoadError onRetry={() => void mcpQuery.refetch()} /> : null}
        {mcpNames.length ? (
          mcpNames.map(name => {
            const server = mcpQuery.data?.[name]
            const globalOff = server?.enabled === false

            return (
              <CapabilityRow
                checked={!globalOff && !agentMcpOff.has(name.toLowerCase())}
                disabled={busyKeys.has(`mcp:${name}`)}
                globalOff={globalOff}
                key={name}
                label={name}
                onToggle={enabled => void toggleMcp(name, enabled)}
              />
            )
          })
        ) : (
          <p className="px-2 py-1 text-[0.7rem] text-(--ui-text-tertiary)">{r.capabilitiesEmpty}</p>
        )}
      </div>
    </div>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n()
  const r = t.rightSidebar

  return (
    <div className="flex items-center justify-between gap-2 px-2 py-1">
      <span className="text-[0.7rem] text-destructive">{r.capabilitiesLoadError}</span>
      <Button aria-label={r.tryAgain} onClick={onRetry} size="icon-xs" variant="ghost">
        <Codicon name="refresh" size="0.75rem" />
      </Button>
    </div>
  )
}

function PaneEmpty({ label }: { label: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-4">
      <SidebarPanelLabel className="pl-0 text-(--ui-text-quaternary)">{label}</SidebarPanelLabel>
    </div>
  )
}
