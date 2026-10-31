import { useStore } from '@nanostores/react'
import { useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ArchiveSkillConfirmDialog } from '@/app/learning/archive-skill-confirm-dialog'
import { CodeEditor } from '@/components/chat/code-editor'
import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CountSkeleton } from '@/components/ui/skeleton'
import {
  editLearningNode,
  getGlobalMcpServers,
  getLearningNode,
  getSkills as getSkillsGlobal,
  getSkillsForAgent,
  getToolsets as getToolsetsGlobal,
  getToolsetsForAgent,
  getUsageAnalytics,
  type PlobiGateway,
  toggleSkill as toggleSkillGlobal,
  toggleSkillForAgent,
  toggleToolset as toggleToolsetGlobal,
  toggleToolsetForAgent
} from '@/plobi'
import { $consoleAgents } from '@/app/console/store/agents'
import type { RosterAgent } from '@/app/settings/agent-model-overrides'
import { putAgentToolPrefs } from '@/app/console/api'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { isDesktopToolsetVisible } from '@/lib/desktop-toolsets'
import { compactNumber } from '@/lib/format'
import { queryClient, writeCache } from '@/lib/query-client'
import { normalize } from '@/lib/text'
import { $gateway } from '@/store/gateway'
import { notify, notifyError } from '@/store/notifications'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import type { SkillInfo, ToolsetInfo } from '@/types/plobi'

import { useOnProfileSwitch } from '../hooks/use-on-profile-switch'
import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import {
  CapRow,
  DetailColumn,
  DetailPane,
  ListColumn,
  ListStrip,
  ListStripButton,
  ListStripMenu,
  type ListStripMenuToggle,
  MasterDetail,
  ToolChip
} from '../master-detail'
import { PanelEmpty, PanelPill } from '../overlays/panel'
import { PageSearchShell } from '../page-search-shell'
import { ComputerUsePanel } from '../settings/computer-use-panel'
import { asText, includesQuery, prettyName, toolNames, toolsetDisplayLabel } from '../settings/helpers'
import { ToolsetConfigPanel } from '../settings/toolset-config-panel'
import type { SetStatusbarItemGroup } from '../shell/statusbar-controls'

import { SkillsHub } from './hub'
import { McpTab } from './mcp-tab'
import { $skillsSortDesc, $toolsetsSortDesc } from './store'

const SKILLS_MODES = ['skills', 'toolsets', 'mcp', 'hub'] as const

// Skills + toolsets live in the RQ cache so switching tabs/pages paints the
// cached lists instantly (no reload flash) and mount only fires a deduped
// background refetch. A profile swap globally invalidates (see store/profile),
// so these plain keys refetch against the new backend automatically.
const SKILLS_QUERY_KEY = ['skills-list'] as const
const TOOLSETS_QUERY_KEY = ['toolsets-list'] as const

// Optimistic write-through: toggles/bulk/archive repaint instantly; the next
// background refetch reconciles with the backend.
const setSkills = writeCache<SkillInfo[]>(SKILLS_QUERY_KEY)
const setToolsets = writeCache<ToolsetInfo[]>(TOOLSETS_QUERY_KEY)

// Per-tool call counts come from a 365-day message scan — heavy, and purely
// cosmetic (Toolsets usage badges). Cache the result module-wide with a TTL so
// bouncing between tabs/pages doesn't re-run the scan every time. Keyed by
// profile: analytics are profile-scoped, so a switch must not show the previous
// profile's counts. `useRefreshHotkey` still forces a fresh pull.
const TOOL_CALLS_TTL_MS = 10 * 60 * 1000
const toolCallsCache = new Map<string, { at: number; value: Record<string, number> }>()

async function loadToolCalls(force = false): Promise<Record<string, number>> {
  const key = normalizeProfileKey($activeGatewayProfile.get())
  const cached = toolCallsCache.get(key)

  if (!force && cached && Date.now() - cached.at < TOOL_CALLS_TTL_MS) {
    return cached.value
  }

  const analytics = await getUsageAnalytics(365)

  const value = Object.fromEntries((analytics.tools ?? []).map(e => [e.tool, e.count]))

  // Only cache if the active profile hasn't changed during the request — else a
  // switch mid-flight would file this result under the wrong profile's key.
  if (normalizeProfileKey($activeGatewayProfile.get()) === key) {
    toolCallsCache.set(key, { at: Date.now(), value })
  }

  return value
}

const usageOf = (skill: SkillInfo): number => (typeof skill.usage === 'number' ? skill.usage : 0)

const categoryFor = (skill: SkillInfo): string => asText(skill.category) || 'general'

// Row subtitle: category, with non-default origins badged.
function skillSubtitle(skill: SkillInfo): React.ReactNode {
  const category = prettyName(categoryFor(skill))
  const provenance = skill.provenance

  return (
    <>
      <span className="truncate">{category}</span>
      {provenance === 'agent' && (
        <Badge className="shrink-0 normal-case" variant="default">
          learned
        </Badge>
      )}
      {provenance === 'hub' && (
        <Badge className="shrink-0 normal-case" variant="muted">
          hub
        </Badge>
      )}
    </>
  )
}

function filteredSkills(skills: SkillInfo[], query: string, desc: boolean): SkillInfo[] {
  const q = normalize(query)
  const sign = desc ? 1 : -1

  return skills
    .filter(
      skill =>
        !q || includesQuery(skill.name, q) || includesQuery(skill.description, q) || includesQuery(skill.category, q)
    )
    .sort((a, b) => sign * (usageOf(b) - usageOf(a)) || asText(a.name).localeCompare(asText(b.name)))
}

const toolsetCalls = (toolset: ToolsetInfo, toolCalls: Record<string, number>): number =>
  toolNames(toolset).reduce((sum, name) => sum + (toolCalls[name] ?? 0), 0)

function filteredToolsets(
  toolsets: ToolsetInfo[],
  query: string,
  toolCalls: Record<string, number>,
  desc: boolean
): ToolsetInfo[] {
  const q = normalize(query)
  const sign = desc ? 1 : -1

  return toolsets
    .filter(toolset => {
      if (!isDesktopToolsetVisible(toolset.name)) {
        return false
      }

      if (!q) {
        return true
      }

      return (
        includesQuery(toolset.name, q) ||
        includesQuery(toolsetDisplayLabel(toolset), q) ||
        includesQuery(toolset.description, q) ||
        toolNames(toolset).some(name => includesQuery(name, q))
      )
    })
    .sort(
      (a, b) =>
        sign * (toolsetCalls(b, toolCalls) - toolsetCalls(a, toolCalls)) ||
        toolsetDisplayLabel(a).localeCompare(toolsetDisplayLabel(b))
    )
}

const visibleToolsetCount = (toolsets: ToolsetInfo[]) => toolsets.filter(ts => isDesktopToolsetVisible(ts.name)).length

interface SkillsViewProps extends React.ComponentProps<'section'> {
  setStatusbarItemGroup?: SetStatusbarItemGroup
}

export function SkillsView({ setStatusbarItemGroup: _setStatusbarItemGroup, ...props }: SkillsViewProps) {
  const { t } = useI18n()
  const gateway = useStore($gateway) as PlobiGateway | null
  const [mode, setMode] = useRouteEnumParam('tab', SKILLS_MODES, 'skills')

  // ─── Agent 作用域（2026-10-20 用户裁定 C；10-31 换键） ───────────────────
  // 带 `?agent=<名册 id>` 进来，这一页就是**这个 Agent 自己的**能力页：四张表
  // 照旧摆着，但读的是它名册行的生效值、写的是它自己的三份黑名单；不带就是
  // 全局真源（只在「设置 → Capabilities」里用）。
  //
  // 键是**名册 id**，不是 profile 家名——profile 是裁 42 / ADR-0013 要拆掉的
  // 概念，产品面上不该再出现（用户 10-31：「不要让 profile 的概念回归」）。
  const { search } = useLocation()
  const scopedAgentId = useMemo(() => {
    const value = new URLSearchParams(search).get('agent')?.trim()

    return value ? value : null
  }, [search])

  const scopedAgent = useStore($consoleAgents).find(agent => agent.id === scopedAgentId)

  // Agent 页要能区分两种「关」：全局关掉的（锁住，开不了）与本 Agent 关掉的（可点开）。
  // 全局那一份只在 Agent 模式才查——全局页本身就是它，不必再查一遍。
  const { data: globalSkillsForScope } = useQuery({
    queryKey: ['capabilities-global-skills'],
    queryFn: getSkillsGlobal,
    enabled: Boolean(scopedAgentId),
    staleTime: 0
  })
  const { data: globalToolsetsForScope } = useQuery({
    queryKey: ['capabilities-global-toolsets'],
    queryFn: getToolsetsGlobal,
    enabled: Boolean(scopedAgentId),
    staleTime: 0
  })

  const globallyOffSkills = useMemo(
    () => new Set((globalSkillsForScope ?? []).filter(row => !row.enabled).map(row => row.name)),
    [globalSkillsForScope]
  )
  const globallyOffToolsets = useMemo(
    () => new Set((globalToolsetsForScope ?? []).filter(row => !row.enabled).map(row => row.name)),
    [globalToolsetsForScope]
  )

  // 名字不变，实现按作用域分叉——下面 500 行调用点一行都不用改。
  const getSkills = useCallback(
    () => (scopedAgentId ? getSkillsForAgent(scopedAgentId) : getSkillsGlobal()),
    [scopedAgentId]
  )
  const getToolsets = useCallback(
    () => (scopedAgentId ? getToolsetsForAgent(scopedAgentId) : getToolsetsGlobal()),
    [scopedAgentId]
  )
  const toggleSkill = useCallback(
    (name: string, enabled: boolean) =>
      scopedAgentId ? toggleSkillForAgent(scopedAgentId, name, enabled) : toggleSkillGlobal(name, enabled),
    [scopedAgentId]
  )
  const toggleToolset = useCallback(
    (name: string, enabled: boolean) =>
      scopedAgentId ? toggleToolsetForAgent(scopedAgentId, name, enabled) : toggleToolsetGlobal(name, enabled),
    [scopedAgentId]
  )

  const [query, setQuery] = useState('')

  const {
    data: skills,
    isError: skillsFailed,
    error: skillsError
  } = useQuery({
    queryKey: SKILLS_QUERY_KEY,
    queryFn: getSkills,
    staleTime: 0
  })

  const { data: toolsets, isError: toolsetsFailed } = useQuery({
    queryKey: TOOLSETS_QUERY_KEY,
    queryFn: getToolsets,
    staleTime: 0
  })

  // 缓存键仍是全局那一把（模块级的 setSkills/setToolsets 绑在它上面，换键会把
  // 乐观写入写到另一格去）。所以作用域一换就显式重取一次：全局页与某个 Agent 的
  // 页不会同时存在，这一取就是对的读数。
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: SKILLS_QUERY_KEY })
    void queryClient.invalidateQueries({ queryKey: TOOLSETS_QUERY_KEY })
  }, [scopedAgentId])

  // tool name -> call count over the analytics window. null = still loading
  // (badges show skeletons); {} = loaded empty / unavailable backend.
  const [toolCalls, setToolCalls] = useState<Record<string, number> | null>(null)
  // Bumped on profile switch so a slow analytics load from profile A can't set
  // toolCalls after the user moved to B.
  const toolCallsEpoch = useRef(0)
  const skillsSortDesc = useStore($skillsSortDesc)
  const toolsetsSortDesc = useStore($toolsetsSortDesc)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null)
  const [selectedToolset, setSelectedToolset] = useState<string | null>(null)

  const refreshCapabilities = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: SKILLS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: TOOLSETS_QUERY_KEY })
    ])

    // An explicit refresh is the one time we bypass the analytics TTL — but
    // only if the badges are already on screen; otherwise let the lazy load
    // pick it up when Toolsets is first shown. Guard the async set against a
    // profile switch landing before it resolves.
    if (toolCallsCache.size > 0) {
      const epoch = toolCallsEpoch.current

      loadToolCalls(true)
        .then(value => toolCallsEpoch.current === epoch && setToolCalls(value))
        .catch(() => toolCallsEpoch.current === epoch && setToolCalls({}))
    }
  }, [])

  const refreshToolsets = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: TOOLSETS_QUERY_KEY })
  }, [])

  useRefreshHotkey(refreshCapabilities)

  // Per-tool call counts feed ONLY the Toolsets tab's usage badges/sort, and
  // the query behind them is a 365-day message scan — heavy. Fetch it lazily
  // the first time Toolsets is shown, never on Skills or MCP, so it can't
  // starve the MCP tab's config load. Absent → toolsets sort A–Z until it lands.
  useEffect(() => {
    if (mode !== 'toolsets' || toolCalls !== null) {
      return
    }

    let cancelled = false
    // Guard the setter by epoch too: when toolCalls is already null at switch
    // time, setToolCalls(null) is a no-op so this effect never re-runs to flip
    // `cancelled` — the epoch check catches that gap.
    const epoch = toolCallsEpoch.current
    const live = () => !cancelled && toolCallsEpoch.current === epoch

    loadToolCalls()
      .then(value => live() && setToolCalls(value))
      .catch(() => live() && setToolCalls({}))

    return () => void (cancelled = true)
  }, [mode, toolCalls])

  // On a profile switch the analytics cache is profile-keyed, but our local
  // toolCalls state isn't — leaving it non-null would keep the lazy effect from
  // ever re-running, so badges/sort would show the previous profile's counts.
  // Reset to null so the next Toolsets view reloads for the active profile.
  useOnProfileSwitch(() => {
    toolCallsEpoch.current += 1
    setToolCalls(null)
  })

  const visibleSkills = useMemo(
    () => (skills ? filteredSkills(skills, query, skillsSortDesc) : []),
    [query, skills, skillsSortDesc]
  )

  const visibleToolsets = useMemo(
    () => (toolsets ? filteredToolsets(toolsets, query, toolCalls ?? {}, toolsetsSortDesc) : []),
    [query, toolCalls, toolsets, toolsetsSortDesc]
  )

  // Bulk actions ("All" master switch, "Disable unused") and the master-switch
  // state target the WHOLE tab, never the search-filtered view — a tab-wide
  // control that silently scoped to the current query would be a lie.
  const bulkSkills = skills ?? []
  const bulkToolsets = useMemo(() => (toolsets ?? []).filter(ts => isDesktopToolsetVisible(ts.name)), [toolsets])

  // Rotating placeholder nudges from the user's own data — teach that search
  // understands categories and tool names, not just titles.
  const searchHints = useMemo(() => {
    if (mode === 'skills' && skills?.length) {
      const counts = new Map<string, number>()

      for (const skill of skills) {
        const key = categoryFor(skill)
        counts.set(key, (counts.get(key) || 0) + 1)
      }

      return [...counts.entries()]
        .sort(([, a], [, b]) => b - a)
        .slice(0, 5)
        .map(([category]) => t.common.tryHint(category.toLowerCase()))
    }

    if (mode === 'toolsets' && toolsets?.length) {
      return toolsets
        .filter(ts => isDesktopToolsetVisible(ts.name) && toolNames(ts).length > 0)
        .slice(0, 5)
        .map(ts => t.common.tryHint(toolNames(ts)[0]))
    }

    return undefined
  }, [mode, skills, toolsets, t])

  // Keep a valid selection: fall back to the first visible row when the
  // current selection is filtered out (or nothing is selected yet).
  const activeSkill = useMemo(
    () => visibleSkills.find(s => s.name === selectedSkill) ?? visibleSkills[0] ?? null,
    [selectedSkill, visibleSkills]
  )

  const activeToolset = useMemo(
    () => visibleToolsets.find(ts => ts.name === selectedToolset) ?? visibleToolsets[0] ?? null,
    [selectedToolset, visibleToolsets]
  )

  // Single toggles are optimistic and silent on success (the row repaints
  // immediately — a toast per flip would spam rapid customization). Errors
  // revert and notify.
  async function handleToggleSkill(skill: SkillInfo, enabled: boolean) {
    setSkills(current => current?.map(row => (row.name === skill.name ? { ...row, enabled } : row)) ?? current)

    try {
      await toggleSkill(skill.name, enabled)
    } catch (err) {
      setSkills(
        current => current?.map(row => (row.name === skill.name ? { ...row, enabled: !enabled } : row)) ?? current
      )
      notifyError(err, t.skills.failedToUpdate(skill.name))
    }
  }

  async function handleToggleToolset(toolset: ToolsetInfo, enabled: boolean) {
    setToolsets(
      current =>
        current?.map(row => (row.name === toolset.name ? { ...row, enabled, available: enabled } : row)) ?? current
    )

    try {
      await toggleToolset(toolset.name, enabled)
    } catch (err) {
      setToolsets(
        current =>
          current?.map(row => (row.name === toolset.name ? { ...row, enabled: !enabled, available: !enabled } : row)) ??
          current
      )
      notifyError(err, t.skills.failedToUpdate(toolsetDisplayLabel(toolset)))
    }
  }

  // Sequential on purpose: each toggle is a config read-modify-write on the
  // backend; parallel calls would race the disabled-list save.
  async function bulkApply(skillTargets: SkillInfo[], toolsetTargets: ToolsetInfo[], enabled: boolean) {
    if (bulkBusy || skillTargets.length + toolsetTargets.length === 0) {
      return
    }

    setBulkBusy(true)

    let done = 0

    try {
      for (const row of skillTargets) {
        await toggleSkill(row.name, enabled)
        setSkills(cur => cur?.map(r => (r.name === row.name ? { ...r, enabled } : r)) ?? cur)
        done += 1
      }

      for (const row of toolsetTargets) {
        await toggleToolset(row.name, enabled)
        setToolsets(cur => cur?.map(r => (r.name === row.name ? { ...r, enabled, available: enabled } : r)) ?? cur)
        done += 1
      }

      notify({ kind: 'success', title: t.skills.bulkUpdated(done), message: '' })
    } catch (err) {
      notifyError(err, t.skills.failedToUpdate(mode === 'skills' ? t.skills.tabSkills : t.skills.tabToolsets))
    } finally {
      setBulkBusy(false)
    }
  }

  const bulkToggle = (enabled: boolean) =>
    mode === 'skills'
      ? bulkApply(
          bulkSkills.filter(row => row.enabled !== enabled),
          [],
          enabled
        )
      : bulkApply(
          [],
          bulkToolsets.filter(row => row.enabled !== enabled),
          enabled
        )

  // "Never used" = zero recorded activity. The pruning move for a 100+ skill
  // install: keep the workhorses, shed the noise.
  const disableUnused = () =>
    bulkApply(
      bulkSkills.filter(skill => skill.enabled && usageOf(skill) === 0),
      [],
      false
    )

  // One switch line covering enable-all/disable-all.
  const bulkSwitch = (allEnabled: boolean): ListStripMenuToggle => ({
    checked: allEnabled,
    disabled: bulkBusy,
    label: t.skills.all,
    onToggle: checked => void bulkToggle(checked)
  })

  const allSkillsEnabled = bulkSkills.length > 0 && bulkSkills.every(s => s.enabled)
  const allToolsetsEnabled = bulkToolsets.length > 0 && bulkToolsets.every(ts => ts.enabled)

  const sortButton = (desc: boolean, flip: () => void) => (
    <ListStripButton onClick={flip}>{desc ? t.skills.sortMostUsedDesc : t.skills.sortLeastUsedAsc}</ListStripButton>
  )

  // Full-bleed empty state, matching the MCP tab (spans both columns, not a
  // cramped note in the left rail). Query-aware, and says "tools" not the
  // internal "toolsets".
  const capabilityEmpty = (noun: string) => {
    const q = query.trim()

    return (
      <div className="flex h-full min-h-0 flex-1">
        <PanelEmpty
          description={q ? t.skills.emptyNothingMatches(q) : t.skills.emptyNoneAvailable(noun)}
          icon="search"
          title={t.skills.emptyNoneFound(noun)}
        />
      </div>
    )
  }

  // Learned/local skills are editable + archivable, mirroring the memory
  // graph (same /api/learning/node endpoints — delete archives, restorable
  // via `plobi curator restore`).
  const [skillEditor, setSkillEditor] = useState<null | { content: string; name: string }>(null)
  const [skillDraft, setSkillDraft] = useState('')
  const [skillSaving, setSkillSaving] = useState(false)
  const [archiveTarget, setArchiveTarget] = useState<null | string>(null)
  // Bumped on profile switch so an in-flight openSkillEditor fetch from profile
  // A can't reopen the editor with A's content after switching to B.
  const skillEditorEpoch = useRef(0)

  // A profile switch swaps the backend under the open editor/archive dialog —
  // their targets belong to profile A, so a save/archive would hit B. Drop them
  // so nothing edits or archives against the newly active profile.
  useOnProfileSwitch(() => {
    skillEditorEpoch.current += 1
    setSkillEditor(null)
    setSkillDraft('')
    setArchiveTarget(null)
  })

  const openSkillEditor = async (name: string) => {
    const epoch = skillEditorEpoch.current

    try {
      const node = await getLearningNode(name)

      if (skillEditorEpoch.current !== epoch) {
        return
      }

      setSkillEditor({ content: node.content, name })
      setSkillDraft(node.content)
    } catch (err) {
      notifyError(err, name)
    }
  }

  const saveSkillEdit = async () => {
    if (!skillEditor) {
      return
    }

    setSkillSaving(true)

    try {
      await editLearningNode(skillEditor.name, skillDraft)
      notify({
        kind: 'success',
        title: t.skills.skillUpdated,
        message: t.skills.appliesToNewSessions(skillEditor.name)
      })
      setSkillEditor(null)
      void refreshCapabilities()
    } catch (err) {
      notifyError(err, skillEditor.name)
    } finally {
      setSkillSaving(false)
    }
  }

  const skillEditorPane = skillEditor && (
    <DetailPane
      actions={
        <Button disabled={skillSaving} onClick={() => void saveSkillEdit()} size="xs">
          {skillSaving ? t.common.saving : t.common.save}
        </Button>
      }
      id="skill-editor"
      onClose={() => setSkillEditor(null)}
      title={<span className="text-[0.68rem] font-normal text-muted-foreground/60">{skillEditor.name}/SKILL.md</span>}
    >
      <CodeEditor
        filePath="SKILL.md"
        initialValue={skillEditor.content}
        key={skillEditor.name}
        onCancel={() => setSkillEditor(null)}
        onChange={setSkillDraft}
        onSave={() => void saveSkillEdit()}
      />
    </DetailPane>
  )

  return (
    <PageSearchShell
      {...props}
      activeTab={mode}
      onSearchChange={setQuery}
      onTabChange={id => setMode(id as (typeof SKILLS_MODES)[number])}
      // MCP manages a handful of entries with the editor right there —
      // searching it is noise.
      searchHidden={mode === 'mcp'}
      searchHints={searchHints}
      searchPlaceholder={
        mode === 'skills'
          ? t.skills.searchSkills
          : mode === 'hub'
            ? t.skills.hub.searchPlaceholder
            : t.skills.searchToolsets
      }
      searchValue={query}
      tabs={[
        { id: 'skills', label: t.skills.tabSkills, meta: skills?.length ?? null },
        { id: 'toolsets', label: t.skills.tabToolsets, meta: toolsets ? visibleToolsetCount(toolsets) : null },
        { id: 'mcp', label: t.skills.tabMcp },
        { id: 'hub', label: t.skills.tabHub }
      ]}
    >
      {mode === 'hub' ? (
        <SkillsHub query={query} />
      ) : mode === 'mcp' ? (
        scopedAgent ? (
          <AgentMcpTab agent={scopedAgent} />
        ) : (
          <McpTab gateway={gateway} />
        )
      ) : (skillsFailed || toolsetsFailed) && (!skills || !toolsets) ? (
        <PanelEmpty
          action={
            <Button onClick={() => void refreshCapabilities()} size="sm">
              {t.skills.refresh}
            </Button>
          }
          description={skillsError instanceof Error ? skillsError.message : undefined}
          icon="error"
          title={t.skills.skillsLoadFailed}
        />
      ) : !skills || !toolsets ? (
        <PageLoader label={t.skills.loading} />
      ) : mode === 'skills' ? (
        visibleSkills.length === 0 ? (
          capabilityEmpty('skills')
        ) : (
          <MasterDetail pane={skillEditorPane} split="wide">
            <ListColumn
              header={
                <ListStrip
                  left={sortButton(skillsSortDesc, () => $skillsSortDesc.set(!$skillsSortDesc.get()))}
                  right={
                    <ListStripMenu
                      items={[
                        { disabled: bulkBusy, label: t.skills.disableUnused, onSelect: () => void disableUnused() }
                      ]}
                      label={t.skills.tabSkills}
                      toggle={bulkSwitch(allSkillsEnabled)}
                    />
                  }
                />
              }
            >
              {visibleSkills.map(skill => (
                <CapRow
                  active={activeSkill?.name === skill.name}
                  busy={bulkBusy}
                  enabled={skill.enabled}
                  key={skill.name}
                  locked={Boolean(scopedAgentId) && globallyOffSkills.has(skill.name)}
                  meta={usageOf(skill) > 0 ? `×${compactNumber(usageOf(skill))}` : undefined}
                  onSelect={() => setSelectedSkill(skill.name)}
                  onToggle={enabled => void handleToggleSkill(skill, enabled)}
                  subtitle={skillSubtitle(skill)}
                  title={skill.name}
                  toggleLabel={skill.name}
                />
              ))}
            </ListColumn>
            <DetailColumn footer={t.skills.changesApplyNewSessions}>
              {activeSkill && (
                <SkillDetail
                  onArchive={() => setArchiveTarget(activeSkill.name)}
                  onEdit={() => void openSkillEditor(activeSkill.name)}
                  skill={activeSkill}
                />
              )}
            </DetailColumn>
          </MasterDetail>
        )
      ) : visibleToolsets.length === 0 ? (
        capabilityEmpty('tools')
      ) : (
        <MasterDetail split="wide">
          <ListColumn
            header={
              <ListStrip
                left={sortButton(toolsetsSortDesc, () => $toolsetsSortDesc.set(!$toolsetsSortDesc.get()))}
                right={<ListStripMenu label={t.skills.tabToolsets} toggle={bulkSwitch(allToolsetsEnabled)} />}
              />
            }
          >
            {visibleToolsets.map(toolset => {
              const label = toolsetDisplayLabel(toolset)
              const calls = toolCalls ? toolsetCalls(toolset, toolCalls) : null

              return (
                <CapRow
                  active={activeToolset?.name === toolset.name}
                  busy={bulkBusy}
                  enabled={toolset.enabled}
                  key={toolset.name}
                  locked={Boolean(scopedAgentId) && globallyOffToolsets.has(toolset.name)}
                  meta={
                    calls === null ? (
                      <CountSkeleton />
                    ) : calls > 0 ? (
                      `×${compactNumber(calls)}`
                    ) : (
                      `${toolNames(toolset).length} tools`
                    )
                  }
                  onSelect={() => setSelectedToolset(toolset.name)}
                  onToggle={checked => void handleToggleToolset(toolset, checked)}
                  subtitle={asText(toolset.description)}
                  title={label}
                  toggleLabel={t.skills.toggleToolset(label)}
                />
              )
            })}
          </ListColumn>
          <DetailColumn footer={t.skills.changesApplyNewSessions}>
            {activeToolset && (
              <ToolsetDetail onConfiguredChange={refreshToolsets} toolCalls={toolCalls ?? {}} toolset={activeToolset} />
            )}
          </DetailColumn>
        </MasterDetail>
      )}
      {archiveTarget && (
        <ArchiveSkillConfirmDialog
          onApply={() => {
            const name = archiveTarget
            const snapshot = skills

            setSkills(current => current?.filter(skill => skill.name !== name) ?? current)

            if (skillEditor?.name === name) {
              setSkillEditor(null)
            }

            return () => setSkills(snapshot)
          }}
          onClose={() => setArchiveTarget(null)}
          onFailure={(err, name) => notifyError(err, name)}
          open
          skillId={archiveTarget}
          skillName={archiveTarget}
        />
      )}
    </PageSearchShell>
  )
}

// Shared inspector header — mirrors Messaging's PlatformDetail so Skills and
// Tools share one title/description block and tab switches don't jump.
/**
 * 某个 Agent 的 MCP 一栏（2026-10-20 用户裁定 C）。
 *
 * 全局名单是唯一真源（`mcp_servers`），这里只画**这个 Agent 关掉了哪些**：
 * 读全局名单 + 名册行的 `disabledMcpServers`，写走 `PUT /api/agents/:id/tool-prefs`
 * （整份回写，端点是 PUT 语义）。全局关掉的照旧置灰并标出来——这个 Agent 只能再关
 * 自己那份，不能替全局开。
 */
function AgentMcpTab({ agent }: { agent: RosterAgent }) {
  const { t } = useI18n()
  const m = t.settings.mcp
  const { data: servers } = useQuery({ queryKey: ['agent-capabilities', 'mcp-servers'], queryFn: getGlobalMcpServers, staleTime: 0 })
  const [busy, setBusy] = useState<string | null>(null)
  const off = new Set((agent.disabledMcpServers ?? []).map(name => name.toLowerCase()))
  const names = Object.keys(servers ?? {})

  const toggle = async (name: string, enabled: boolean) => {
    const next = new Set(agent.disabledMcpServers ?? [])
    const matched = [...next].find(key => key.toLowerCase() === name.toLowerCase())

    if (enabled) {
      if (matched) {
        next.delete(matched)
      }
    } else {
      next.add(matched ?? name)
    }

    setBusy(name)

    try {
      await putAgentToolPrefs(agent.id, { disabledMcpServers: [...next] })
    } catch (err) {
      notifyError(err, t.rightSidebar.capabilitiesToggleFailed)
    } finally {
      setBusy(null)
    }
  }

  if (names.length === 0) {
    return <PanelEmpty icon="info" title={m.emptyTitle} description={m.emptyDesc} />
  }

  return (
    <div className="px-1.5 py-1">
      {names.map(name => {
        const globalOff = servers?.[name]?.enabled === false
        const checked = !globalOff && !off.has(name.toLowerCase())

        return (
          <div
            className="flex h-8 items-center justify-between gap-2 rounded-md px-2 text-[0.78rem] text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background)"
            key={name}
          >
            <span className="min-w-0 flex-1 truncate">{name}</span>
            <Switch
              checked={checked}
              disabled={busy === name || globalOff}
              onCheckedChange={next => void toggle(name, next)}
            />
          </div>
        )
      })}
    </div>
  )
}

function DetailHeader({
  description,
  pills,
  title
}: {
  description: React.ReactNode
  pills?: React.ReactNode
  title: string
}) {
  return (
    <header>
      <div className="flex min-h-6 flex-wrap items-center gap-2">
        <h3 className="min-w-0 truncate text-[0.9375rem] font-semibold tracking-tight">{title}</h3>
        {pills}
      </div>
      <p className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
        {description}
      </p>
    </header>
  )
}

function SkillDetail({ onArchive, onEdit, skill }: { onArchive: () => void; onEdit: () => void; skill: SkillInfo }) {
  const { t } = useI18n()
  // Only learned/local skills are the user's to rewrite or archive — bundled
  // and hub skills are managed by their sources.
  const editable = skill.provenance === 'agent'

  return (
    <>
      <DetailHeader
        description={asText(skill.description) || t.skills.noDescription}
        pills={
          <>
            <PanelPill>{prettyName(categoryFor(skill))}</PanelPill>
            {skill.provenance && skill.provenance !== 'bundled' && (
              <PanelPill tone={skill.provenance === 'agent' ? 'good' : 'muted'}>
                {t.skills.provenance[skill.provenance]}
              </PanelPill>
            )}
          </>
        }
        title={skill.name}
      />
      {editable && (
        <div className="flex items-center gap-2">
          <Button onClick={onEdit} size="xs" variant="text">
            {t.skills.edit}
          </Button>
          <Button className="text-destructive hover:text-destructive" onClick={onArchive} size="xs" variant="text">
            {t.skills.archive}
          </Button>
        </div>
      )}
    </>
  )
}

function ToolsetDetail({
  toolset,
  toolCalls,
  onConfiguredChange
}: {
  toolset: ToolsetInfo
  toolCalls: Record<string, number>
  onConfiguredChange: () => void
}) {
  const { t } = useI18n()
  const tools = toolNames(toolset)
  const label = toolsetDisplayLabel(toolset)

  return (
    <>
      {/* "Configured" as a resting state is noise — only the warn state earns a pill. */}
      <DetailHeader
        description={asText(toolset.description) || t.skills.noDescription}
        pills={!toolset.configured && <PanelPill tone="warn">{t.skills.needsKeys}</PanelPill>}
        title={label}
      />
      {tools.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {tools.map(name => (
            <ToolChip key={name}>
              {name}
              {(toolCalls[name] ?? 0) > 0 && (
                <span className="ml-1 text-(--ui-text-quaternary)">×{compactNumber(toolCalls[name])}</span>
              )}
            </ToolChip>
          ))}
        </div>
      )}
      {toolset.name === 'computer_use' && <ComputerUsePanel onConfiguredChange={onConfiguredChange} />}
      <ToolsetConfigPanel key={toolset.name} onConfiguredChange={onConfiguredChange} toolset={toolset.name} />
    </>
  )
}
