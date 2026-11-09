import { useEffect, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { getAgentOffloads, offloadResume } from '@/plobi'
import { openConversation } from '@/store/conversation-view'

import { externalHandleOf, groupOutsourced, jumpTargetFor, outsourcedItems, roundFacts } from './agent-offloads'
import type { OffloadRoundWithReply } from './agent-offloads'
import { SidebarDrawerRow } from './chrome'
import { ExpertRoleRow, useAgentExperts } from './expert-panel'
import { EXPERT_ROLES, latestRun, runsOfRole } from './expert-roles'
import { unreadableSources, vendorLabel } from './external-sessions'
import { ExternalSessionRow, useExternalSessions } from './external-sessions-panel'
import { VendorBrandBlock } from './offload-vendor-icon'

/**
 * 点进一个 L2 时，左栏中段可以切到这一格：**这一摊的活派给了谁**。
 *
 * 11-01 第二次改形（用户三张图定点）：
 *
 * 1. 两个一级**抽屉**：「专家」和「外包」，都是「矢量图标 + 名词 + 计数 + 折角」，
 *    和分类行同一套形状——不再是那种小字分区标题。
 * 2. 「专家」下面放的是**四类角色的名册**（文件 / 浏览器 / 软件 / 电脑），不是会话流水
 *    （用户 11-01：「专家下面不要放会话了」）。点一行就打开这个角色最近那条会话——
 *    用户选定的形态是「一个专家一张嘴」。
 * 3. 「外包」下面还是三级：抽屉 → 那家软件 → 它底下这一摊的所有会话。
 * 4. 点一行**不再弹窗**：交给 `$openConversation`，主区用对话卡片显示（`ConversationPane`）。
 *
 * 两路数据各自自读：台账（`GET /api/agents/:id/offloads`）与外部会话
 * （`GET /api/agents/:id/external-sessions`）。
 */
/** 开着这一格时的重读间隔。台账是一行一个 JSON，读它比轮询一次网络便宜得多。 */
const REFRESH_MS = 5000

export function AgentOffloadPanel({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const c = t.console
  const [rounds, setRounds] = useState<null | OffloadRoundWithReply[]>(null)
  const [expertsOpen, setExpertsOpen] = useState(true)
  const [outsourcedOpen, setOutsourcedOpen] = useState(true)
  const [openVendor, setOpenVendor] = useState<null | string>(null)
  // 「待判断」那批**默认展开**（用户 11-09）：Marvis 那 11 条里只有两条自动判成有关，
  // 藏在一枚裸数字后面 ⇒ 别的 Agent 打开这一格几乎看不见东西。那枚眼睛还在，可收起来。
  const [showUnknown, setShowUnknown] = useState(true)
  const [showArchived, setShowArchived] = useState(false)
  const experts = useAgentExperts(agentId)
  const { data, reload: reloadExternal } = useExternalSessions(agentId)

  useEffect(() => {
    let cancelled = false

    const load = (first: boolean) => {
      if (first) {
        setRounds(null)
      }

      getAgentOffloads(agentId)
        .then(body => {
          if (!cancelled) {
            setRounds(body?.data?.rounds ?? [])
          }
        })
        .catch(() => {
          // 已经读到过的表不清空：一次抖动不该把"它外包过什么"抹成读取失败。
          if (!cancelled && first) {
            setRounds([])
          }
        })
    }

    load(true)
    const timer = setInterval(() => load(false), REFRESH_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [agentId])

  const all = data?.sessions ?? []
  const live = all.filter(session => !session.archived)
  const archived = all.filter(session => session.archived)
  const related = live.filter(session => session.related === true)
  const unknown = live.filter(session => session.related === null)
  // 一条相关的都没有时，先把「待判断」摆出来（按应用分组）——否则第一次打开只是一句空话。
  const visible = showUnknown ? [...related, ...unknown] : related
  const groups = groupOutsourced(rounds ?? [], visible)
  const shownCount = groups.reduce((sum, group) => sum + group.rounds.length + group.sessions.length, 0)
  // 抽屉头上的数 = 这一格**点得到**的全部东西：展开着的那些 + 眼睛后面那批 + 已归档的。
  //
  // 归档与待判断必须算进来（11-01 修）：只数展开着的那部分时，一旦把最后一条会话归档，
  // 抽屉当场消失——「已归档」那个开关跟着没了，收回去的东西再也捞不回来。
  const total = shownCount + (showUnknown ? 0 : unknown.length) + archived.length

  // 库里在、但这台机器上没留下可读对话的那家（现读＝Cursor）：**照实说一行人话**，
  // 句子直接用后端给的那句（屏上不另写）。已经有分组的那家不重复说——它自己就在表里。
  const unreadable = unreadableSources(data?.sources).filter(
    source => !groups.some(group => group.vendor === source.vendor)
  )

  if (rounds === null) {
    return null
  }

  return (
    <div className="flex flex-col gap-px">
      {/* 专家：四类角色的名册。**不放会话**——会话是点开这一行之后主区里的事。
          一个都没派过时不印「0」：名册照样在，只是四行都灰着。 */}
      <SidebarDrawerRow
        count={experts.length || undefined}
        icon="person"
        label={c.expertsTitle}
        onToggle={() => setExpertsOpen(value => !value)}
        open={expertsOpen}
      >
        <div className="flex flex-col gap-px pl-4">
          {EXPERT_ROLES.map(role => {
            const runs = runsOfRole(experts, role.key)
            const latest = latestRun(runs)

            return (
              <ExpertRoleRow
                key={role.key}
                // 没跑过也照开：主区那张卡会写清这个角色是什么、怎么让它出场。
                onOpen={() =>
                  openConversation({
                    agentId,
                    kind: 'expert',
                    role: role.key,
                    running: latest?.status === 'running',
                    sessionId: latest?.id ?? null,
                    title: latest?.goal || t.console[role.labelKey]
                  })}
                role={role}
                runs={runs}
              />
            )
          })}
        </div>
      </SidebarDrawerRow>

      {(total > 0 || unreadable.length > 0) && (
        <SidebarDrawerRow
          count={total}
          icon="send"
          label={c.offloadsTitle}
          onToggle={() => setOutsourcedOpen(value => !value)}
          open={outsourcedOpen}
        >
          <div className="flex flex-col gap-px pl-4">
            {groups.map(group => {
              const expanded = openVendor === group.vendor

              return (
                <div key={group.vendor}>
                  {/* 软件行：**这家软件自己的图标 + 名字 + 折角**（用户 11-01：
                      「外包软件的抽屉就显示软件对应的图标就行了…还是要有名称和折角的」）。
                      不再放会话数——那个数在一级抽屉头上已经有了，这里重复一遍只是噪音。 */}
                  <button
                    className="group/outsourced flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-[0.8125rem] font-medium text-(--ui-text-secondary) transition-colors duration-100 ease-out hover:bg-(--ui-control-hover-background) hover:text-foreground hover:transition-none"
                    onClick={() => setOpenVendor(expanded ? null : group.vendor)}
                    title={group.label}
                    type="button"
                  >
                    <VendorBrandBlock vendor={group.vendor} />
                    <span className="min-w-0 flex-1 truncate">{group.label}</span>
                    <Codicon
                      className="shrink-0 text-(--ui-text-tertiary) opacity-60"
                      name={expanded ? 'chevron-down' : 'chevron-right'}
                      size="0.75rem"
                    />
                  </button>

                  {expanded && (
                    <div className="flex flex-col gap-px pl-5">
                      {outsourcedItems(group).map(item =>
                        item.kind === 'round' ? (
                          <OffloadRoundRow key={`${item.round.ts}-${item.round.cwd}`} round={item.round} />
                        ) : (
                          <ExternalSessionRow
                            agentId={agentId}
                            key={`${item.session.vendor}-${item.session.id}`}
                            onMarked={reloadExternal}
                            onOpen={() =>
                              openConversation({
                                agentId,
                                kind: 'external',
                                sendable: item.session.sendable,
                                sessionId: item.session.id,
                                title: item.session.title || item.session.id.slice(0, 8),
                                updatedAt: item.session.updated_at,
                                vendor: item.session.vendor
                              })}
                            // 还没判断的那一批：整行压暗一档（形状说它还没定性，不写「待判断」上屏）。
                            pending={item.session.related === null}
                            session={item.session}
                          />
                        )
                      )}
                    </div>
                  )}
                </div>
              )
            })}

            {/* 照实说：库里在、但这台机器上没留下可读对话的那家，一行人话——
                句子是后端给的那句原话（屏上不另写，也不改写成「它没有对话」）。 */}
            {unreadable.map(source => (
              <div
                className="flex items-center gap-2 rounded-md px-2 py-1"
                data-testid={`external-session-unreadable-${source.vendor}`}
                key={source.vendor}
              >
                <VendorBrandBlock vendor={source.vendor} />
                <span className="min-w-0 flex-1 truncate text-[0.7rem] text-(--ui-text-tertiary)">
                  {source.reason}
                </span>
              </div>
            ))}

            {unknown.length > 0 && (
              <button
                aria-label={showUnknown ? c.externalSessionsHideUnknown : c.externalSessionsShowUnknown}
                className={cn(
                  'flex items-center gap-1 rounded-md px-2 py-0.5 text-left text-[0.68rem] hover:bg-(--ui-control-hover-background)',
                  showUnknown ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-quaternary)'
                )}
                onClick={() => setShowUnknown(value => !value)}
                title={showUnknown ? c.externalSessionsHideUnknown : c.externalSessionsShowUnknown}
                type="button"
              >
                <Codicon name={showUnknown ? 'eye' : 'eye-closed'} size="0.75rem" />
                {unknown.length}
              </button>
            )}

            {archived.length > 0 && (
              <div className="flex flex-col gap-px">
                <button
                  aria-label={c.externalSessionsArchived}
                  className={cn(
                    'flex items-center gap-1 rounded-md px-2 py-0.5 text-left text-[0.68rem] hover:bg-(--ui-control-hover-background)',
                    showArchived ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-quaternary)'
                  )}
                  onClick={() => setShowArchived(value => !value)}
                  title={c.externalSessionsArchived}
                  type="button"
                >
                  <Codicon name="archive" size="0.75rem" />
                  {archived.length}
                </button>
                {showArchived &&
                  archived.map(session => (
                    <ExternalSessionRow
                      key={`archived-${session.vendor}-${session.id}`}
                      muted
                      onOpen={() =>
                        openConversation({
                          agentId,
                          kind: 'external',
                          sendable: session.sendable,
                          sessionId: session.id,
                          title: session.title || session.id.slice(0, 8),
                          updatedAt: session.updated_at,
                          vendor: session.vendor
                        })}
                      session={session}
                      trailing={vendorLabel(session.vendor)}
                    />
                  ))}
              </div>
            )}
          </div>
        </SidebarDrawerRow>
      )}
    </div>
  )
}

function OffloadRoundRow({ round }: { round: OffloadRoundWithReply }) {
  const { t } = useI18n()
  const c = t.console
  const plan = jumpTargetFor(round)
  const facts = roundFacts(round)
  const clickable = plan.kind !== 'none'
  const [error, setError] = useState('')

  const money = facts.costUsd !== null && facts.costUsd > 0
    ? `$${facts.costUsd}`
    : facts.credits !== null && facts.credits > 0
      ? `${Math.round(facts.credits * 100) / 100} ${c.offloadsCredits}`
      : facts.costUsd !== null
        ? `$${facts.costUsd}`
        : `${c.offloadsCostUnknown}${facts.costStatus ? ` (${facts.costStatus})` : ''}`

  const parts = [
    facts.permissionFlag,
    facts.durationSeconds === null ? '' : `${facts.durationSeconds.toFixed(0)}s`,
    money,
  ].filter(Boolean)

  const reason = plan.reason ? c.offloadsNoArtifact : ''
  const handle = externalHandleOf(round)
  const action = plan.kind === 'resume' ? c.offloadsJump : plan.kind === 'reveal' ? c.offloadsReveal : reason
  const title = [round.cwd, action, round.failure].filter(Boolean).join(' · ')

  const run = () => {
    if (plan.kind === 'resume' && plan.resume) {
      const { agent, sessionId } = plan.resume

      void offloadResume(agent, sessionId)
        .then(() => setError(''))
        .catch((reason: unknown) => setError(jumpFailureText(reason, c.offloadsJumpFailed)))
    } else if (plan.kind === 'reveal') {
      void window.plobiDesktop
        ?.openExternal?.(plan.url)
        .then(() => setError(''))
        .catch(() => setError(c.offloadsOpenFailed))
    }
  }

  return (
    <button
      className={cn(
        'flex w-full flex-col gap-0.5 rounded-md px-2 py-1.5 text-left transition-colors duration-100 ease-out',
        clickable ? 'hover:bg-(--ui-control-hover-background)' : 'cursor-default opacity-70',
      )}
      disabled={!clickable}
      onClick={run}
      title={title}
      type="button"
    >
      <span className="truncate text-[0.8125rem] leading-none text-(--ui-text-secondary)">{round.brief || round.cwd || '—'}</span>
      {round.reply && (
        <span className="line-clamp-3 text-[0.7rem] leading-snug text-(--ui-text-secondary)">
          {c.offloadsReply}
          {round.reply}
        </span>
      )}
      {round.failure && (
        <span className="truncate text-[0.68rem] text-(--ui-red)">{round.failure}</span>
      )}
      {handle && (
        <span className="truncate text-[0.65rem] text-(--ui-text-quaternary)" title={round.external_session_source || undefined}>
          {c.offloadsSessionId}
          {' '}
          {handle}
        </span>
      )}
      <span className="truncate text-[0.68rem] text-(--ui-text-tertiary)">
        {parts.join(' · ')}
        {!clickable && reason ? ` · ${reason}` : ''}
      </span>
      {error && (
        <span className="truncate text-[0.68rem] text-(--ui-red)">{error}</span>
      )}
    </button>
  )
}

/**
 * 后端那四种失败各有一句话（判据 4），但 `fetchJson` 对非 2xx 是 reject，消息形如
 * `400: {"ok":false,"error":"……"}`。这里把那句话从外壳里取出来，取不到就截断原样，
 * 不把 JSON 甩在侧栏那一行上。
 */
function jumpFailureText(reason: unknown, fallback: string): string {
  const raw = reason instanceof Error ? reason.message : String(reason ?? '')
  const start = raw.indexOf('{')

  if (start >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(start)) as { error?: unknown }

      if (typeof parsed.error === 'string' && parsed.error) {
        return parsed.error
      }
    } catch {
      // 不是 JSON 就当原文处理
    }
  }

  return raw ? `${fallback}${raw.slice(0, 120)}` : fallback
}
