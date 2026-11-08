/**
 * 设置 → 外包：一家外部软件一张卡。
 *
 * **它与 Providers / 模型那一屏不是一件事**：那边把订阅/API 借来当模型；这里管的是
 * 「把一段活交给那家软件、让它在自己界面里干」——一个人、一趟活、一条它自己的会话。
 * 外部会话不是另一类东西，它就是这家软件自己的会话；会话入口在对应 Agent 的侧边栏
 * （那边自己调 `/api/external-sessions/sources`），这一屏只管「允不允许把活交给它」。
 *
 * 这一屏的形状（卡片系统统一模板，与三类 provider 卡同形）：
 * - 左边一颗状态点 + 名字 + 一句 i18n 简介（这家软件能外包什么类型的活、适配到哪一步）；
 * - 右边只有两态：**没配好**给加号（点开是下载/登录的引导），**配好了**才是开关；
 * - 能不能用仍由后端一处判据给（`availability()`），这里不自己判第二遍；
 * - 机器细节（二进制、版本、权限档、会话号、后端给的原因）收进悬停提示，卡面只留人话。
 */

import { useCallback, useEffect, useState } from 'react'

import { vendorLabelFor } from '@/app/chat/sidebar/offload-vendor-icon'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { Plus, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'

import { getOffloadAgents, startOffloadLogin, type OffloadAgent, type OffloadPayload, setOffloadAgentEnabled } from './offload/api'
import { ListRow, LoadingState, SettingsContent, SettingsGroup } from './primitives'

const CAPTION =
  'mt-1 text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] text-(--ui-text-tertiary)'

const REASON =
  'mt-1 text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] text-(--ui-text-danger)'

/** UTC ISO → 到分钟，不做时区换算（说真话：后端给什么写什么）。 */
function shortTime(iso: string): string {
  return iso ? iso.slice(0, 16).replace('T', ' ') : ''
}

/** 算不出来就写「—」，不拿 0 冒充当月花了零点几美元（照后端 cost_status 的意思）。 */
function money(value: number | null): string {
  return value === null || value === undefined ? '—' : `$${value.toFixed(2)}`
}

export function OffloadSettings() {
  const { t } = useI18n()
  const s = t.settings.offload

  const [payload, setPayload] = useState<OffloadPayload | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState('')
  const [guide, setGuide] = useState('')

  const load = useCallback(async (refresh = false) => {
    setBusy(true)
    setError('')

    try {
      setPayload(await getOffloadAgents(refresh))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  /**
   * 点「+」时**拉起这一家官方那条登录命令**（用户 11-08 19:4x：「点 + 看看会不会自动拉起登录流程」）。
   *
   * 拉起来了：过几秒重探一次登录态（授权那一下要本人点同意，所以不立刻刷）。
   * 拉不起来：照旧展开那句引导，**屏上不新增句子、不假装拉起了**。
   */
  const launchLogin = useCallback(
    async (agent: OffloadAgent) => {
      try {
        const result = await startOffloadLogin(agent.name)
        if (result.started) {
          window.setTimeout(() => void load(true), 4000)
        }
      } catch {
        // 拉不起来不是错误状态：那一句引导已经写着「去它自己的应用里登录」。
      }
    },
    [load]
  )

  /** 这一格的开关：先就地翻面（点了立刻有反应），落盘失败再翻回去并把原因写在屏上。 */
  const toggle = async (agent: OffloadAgent, next: boolean) => {
    setPending(agent.name)
    setError('')
    setPayload(current =>
      current
        ? { ...current, agents: current.agents.map(item => (item.name === agent.name ? { ...item, enabled: next } : item)) }
        : current
    )

    try {
      await setOffloadAgentEnabled(agent.name, next)
    } catch (err) {
      setPayload(current =>
        current
          ? {
              ...current,
              agents: current.agents.map(item =>
                item.name === agent.name ? { ...item, enabled: agent.enabled } : item
              )
            }
          : current
      )
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setPending('')
    }
  }

  const loginText = (agent: OffloadAgent): string => {
    if (agent.logged_in.value === true) {
      return s.loginYes
    }

    if (agent.logged_in.value === false) {
      return s.loginNo
    }

    return s.loginUnknown
  }

  // 机器细节（二进制、版本、旗标、会话号、后端给的原因）进悬停提示：屏上只留「这是什么 + 现在能不能用」。
  const agentTip = (agent: OffloadAgent): string =>
    [
      agent.installed ? `${agent.binary}${agent.version ? ` · ${agent.version}` : ''}` : s.notInstalled,
      agent.permission_flag,
      loginText(agent),
      agent.why_not,
      agent.resume_supported ? s.resumeYes : s.resumeNo,
      agent.last_round?.external_session ? `${s.sessionId} ${agent.last_round.external_session}` : ''
    ]
      .filter(Boolean)
      .join('\n')

  return (
    <SettingsContent>
      <SettingsGroup
        action={
          <div className="flex items-center gap-2">
            {payload ? (
              <span className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
                {s.probedAt} {shortTime(payload.probed_at)}
              </span>
            ) : null}
            <Button disabled={busy} onClick={() => void load(true)} size="sm" variant="ghost">
              <RefreshCw className={busy ? 'animate-spin' : ''} />
              {s.refresh}
            </Button>
          </div>
        }
        description={s.note}
        title={s.title}
      >
        {error ? <p className="py-2 text-sm text-(--ui-text-danger)">{error}</p> : null}
        {!payload && !error ? <LoadingState label={s.loading} /> : null}
        {payload?.agents.map(agent => {
          // 「配好了」= 装上了、且没判成未登录；能不能用另算（那是后端一处判据）。
          const configured = agent.installed && agent.logged_in.value !== false

          // 自己关掉的那一格不标红、也不搬后端「主人把它关了」那句上屏——那时该说的是「这格是关的」。
          const reason = !configured
            ? agent.installed
              ? s.loginNo
              : s.notInstalled
            : !agent.enabled
              ? ''
              : agent.can_use
                ? ''
                : agent.why_not

          return (
            <ListRow
              action={
                configured ? (
                  <Switch
                    aria-label={`${s.allowSwitch} · ${agent.name}`}
                    checked={agent.enabled}
                    disabled={pending === agent.name}
                    onCheckedChange={next => void toggle(agent, next)}
                  />
                ) : (
                  <Button
                    aria-expanded={guide === agent.name}
                    aria-label={`${s.setup} · ${agent.name}`}
                    onClick={() => {
                      setGuide(current => (current === agent.name ? '' : agent.name))
                      void launchLogin(agent)
                    }}
                    size="icon-sm"
                    type="button"
                    variant="secondary"
                  >
                    <Plus className="size-4" />
                  </Button>
                )
              }
              below={
                <>
                  {reason ? (
                    <p className={REASON} role="note">
                      {reason}
                    </p>
                  ) : null}
                  {configured && !agent.enabled ? (
                    <p className={CAPTION} role="note">
                      {s.switchOff}
                    </p>
                  ) : null}
                  {agent.today.rounds > 0 || agent.today.last_ts ? (
                    <p className={CAPTION}>
                      {s.today} {agent.today.rounds} · {money(agent.today.total_usd)}
                      {agent.today.last_ts ? ` · ${shortTime(agent.today.last_ts)}` : ''}
                    </p>
                  ) : null}
                  {guide === agent.name ? (
                    <p className={CAPTION} role="note">
                      {agent.installed ? s.loginGuide : s.installGuide}
                    </p>
                  ) : null}
                </>
              }
              description={s.intro[agent.name] ?? s.introFallback}
              key={agent.name}
              title={
                <span className="flex min-w-0 items-center gap-2" title={agentTip(agent)}>
                  <span
                    className={cn(
                      'size-2 shrink-0 rounded-full',
                      !configured || !agent.enabled
                        ? 'bg-(--ui-stroke-secondary)'
                        : agent.can_use
                          ? 'bg-primary'
                          : 'bg-destructive'
                    )}
                  />
                  <span className="truncate">{vendorLabelFor(agent.name)}</span>
                </span>
              }
            />
          )
        })}
      </SettingsGroup>
    </SettingsContent>
  )
}
