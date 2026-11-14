/**
 * 设置 → 外包：一家外部软件一张卡。
 *
 * **它与 Providers / 模型那一屏不是一件事**：那边把订阅/API 借来当模型；这里管的是
 * 「把一段活交给那家软件、让它在自己界面里干」——一个人、一趟活、一条它自己的会话。
 * 外部会话不是另一类东西，它就是这家软件自己的会话；会话入口在对应 Agent 的侧边栏
 * （那边自己调 `/api/external-sessions/sources`），这一屏只管「允不允许把活交给它」。
 *
 * 这一屏的形状（卡片系统统一模板，与三类 provider 卡同形）：
 * - 左边**一枚品牌方块**（品牌字形或首字母，与 Providers 那一屏同一颗 `EntityAvatar`）
 *   ＋ 名字 ＋ 一句 i18n 简介（这家软件能外包什么类型的活、适配到哪一步）；
 * - 右边只有两态：**没配好**给加号（点开是下载/登录的引导），**配好了**才是开关；
 * - 状态由控件的形状说话（`+`＝没配好，开关＝配好了），**坏了才在控件旁一颗红点**
 *   ——11-07 统一令第 2 条；从前标题前那颗状态点与右侧控件说的是同一件事，撤掉；
 * - 能不能用仍由后端一处判据给（`availability()`），这里不自己判第二遍；
 * - 机器细节（二进制、版本、权限档、会话号、后端给的原因、今天几轮花了多少）收进悬停提示，
 *   卡面只留人话。
 */

import { useCallback, useEffect, useState } from 'react'

import { vendorLabelFor } from '@/app/chat/sidebar/offload-vendor-icon'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Plus, RefreshCw } from '@/lib/icons'

import { ConnectionControl } from './connection-control'
import { OffloadVendorAvatar } from './offload-vendor-avatar'
import { getOffloadAgents, type OffloadAgent, type OffloadPayload, setOffloadAgentEnabled, startOffloadLogin } from './offload/api'
import { ListRow, LoadingState, SettingsContent, SettingsGroup } from './primitives'

const CAPTION =
  'mt-1 text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] text-(--ui-text-tertiary)'

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

  /**
   * 取数。`silent` 那一档是给**后台轮询**用的：它不该让「再读一次」那颗按钮一直转，
   * 也不该把主人正在看的那句错擦掉。
   */
  const load = useCallback(async (refresh = false, silent = false) => {
    if (!silent) {
      setBusy(true)
      setError('')
    }

    try {
      setPayload(await getOffloadAgents(refresh))
    } catch (err) {
      if (!silent) {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      if (!silent) {
        setBusy(false)
      }
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  /**
   * 后台那一轮 ACP 握手还没答完 ⇒ 隔几秒自己再读一次。
   *
   * 为什么屏上自己会动：登录态现在问的是**厂商自己**（ACP 那一腿），读路径上不许跑探针，
   * 所以答案只能等它落地；落地后这一屏自己就更新（那一格从「＋」长成开关）。
   */
  useEffect(() => {
    if (!payload?.acp_probe_pending) {
      return
    }

    const timer = window.setInterval(() => {
      void load(false, true)
    }, 5000)

    return () => window.clearInterval(timer)
  }, [payload?.acp_probe_pending, load])

  /**
   * 点「+」时**拉起这一家官方那条登录命令**（用户 11-08 19:4x：「点 + 看看会不会自动拉起登录流程」）。
   *
   * 拉起来了：过几秒重探一次登录态（授权那一下要本人点同意，所以不立刻刷）。
   * 拉不起来：照旧展开那句引导，**屏上不新增句子、不假装拉起了**。
   */
  const launchLogin = useCallback(
    async (agent: OffloadAgent) => {
      try {
        await startOffloadLogin(agent.name)
      } catch {
        // 拉不起来不是错误状态：那一句引导已经写着「去它自己的应用里登录」。
      } finally {
        // 拉起成没成成都过几秒重读一次：后端已经问过那家软件了（ACP 那一腿），
        // 它答完的那一格会自己从「＋」长成开关。
        window.setTimeout(() => void load(true), 4000)
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
      // 「本地痕迹」**不是**一次会话的答复：凭据文件在 ≠ 此刻登上了
      // （不许把「有文件」说成「登上了」）⇒ 那一句是「凭据痕迹在，但那只是提示」。
      return agent.logged_in.source === 'local' ? s.hintSaysMaybe : s.loginYes
    }

    if (agent.logged_in.value === false) {
      return s.loginNo
    }

    return s.loginUnknown
  }

  /**
   * 这一格「配好了没有」——**三态，不是两态**（11-14 现读修）。
   *
   * 以前是 `installed && logged_in.value !== false` ⇒ **探不到（None）被算成「配好了」**：
   * 屏上给一颗亮着的开关，主人以为能用，一点上去可能当场撞「它没登录」。
   * 本机现读的三家（codebuddy / opencode / kiro）登录态就是 None ⇒ 三颗假开关。
   *
   * 现在：**探不到 ⇒ 不给开关**，给「＋」＋ 那句「暂无登录状态记录」——
   * **那不是「没登录」**（`loginNo` 说的是「上次连接时显示未登录」，是另一回事、另一句）。
   */
  const configured = (agent: OffloadAgent): boolean =>
    agent.installed && agent.logged_in.value === true

  /** 这一格能不能开：装上了、且**没判成没登录**。探不到（None）也照旧给开关。 */
  const usable = (agent: OffloadAgent): boolean => agent.installed && agent.logged_in.value !== false

  // 机器细节（二进制、版本、旗标、会话号、后端给的原因、今天几轮花了多少）进悬停提示：
  // 屏上只留「这是什么」——状态由控件的形状说话，坏了才在控件旁一颗红点。
  const agentTip = (agent: OffloadAgent): string =>
    [
      agent.installed ? `${agent.binary}${agent.version ? ` · ${agent.version}` : ''}` : s.notInstalled,
      agent.permission_flag,
      loginText(agent),
      // 这一格的证据原话（谁说的、说了什么）——机器细节进悬停，不上屏。
      agent.logged_in.detail,
      agent.why_not,
      agent.resume_supported ? s.resumeYes : s.resumeNo,
      agent.last_round?.external_session ? `${s.sessionId} ${agent.last_round.external_session}` : '',
      // 用量的读数搬进悬停：它不是「这是什么」，留在卡面上只会把这一行撑高，
      // 与 Providers 那些行不是一套形状（卡片语法 §1：一行只有一行正文）。
      agent.today.rounds > 0 || agent.today.last_ts
        ? `${s.today} ${agent.today.rounds} · ${money(agent.today.total_usd)}${
            agent.today.last_ts ? ` · ${shortTime(agent.today.last_ts)}` : ''
          }`
        : '',
      // 自己关掉的那一格不是坏了，是关着——开关的形状已经说了，这句进悬停。
      usable(agent) && !agent.enabled ? s.switchOff : ''
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
        {error ? <p className="py-2 text-sm text-destructive">{error}</p> : null}
        {!payload && !error ? <LoadingState label={s.loading} /> : null}
        {payload?.agents.map(agent => {
          // 「配好了」= 装上了、且**探到登录了**（探不到不算，见上面 `configured`）。
          // 能不能用另算（那是后端一处判据）。
          const isConfigured = configured(agent)

          // 自己关掉的那一格不标红、也不搬后端「主人把它关了」那句上屏——那时该说的是「这格是关的」。
          const reason = !usable(agent)
            ? agent.installed
              ? agent.logged_in.value === false
                ? s.loginNo
                : s.loginUnknown
              : s.notInstalled
            : !agent.enabled
              ? ''
              : agent.can_use
                ? ''
                : agent.why_not

          // 「坏了」＝开着却服务不了这一回合：控件不变（开关仍然可点），旁边多一颗红点，
          // 原因进悬停（11-07 统一令：形状说话，坏了才红点）。
          const broken = usable(agent) && agent.enabled && !agent.can_use

          return (
            <ListRow
              action={
                isConfigured ? (
                  /* 右侧那**一颗**控件（`ConnectionControl`，与 Providers/Sources 同一件）：
                     配好了是开关；坏了不换控件，旁边一颗红点。 */
                  <ConnectionControl
                    busy={pending === agent.name}
                    checked={agent.enabled}
                    configureLabel={s.setup}
                    error={broken}
                    errorTitle={reason}
                    mode="toggle"
                    onToggle={next => void toggle(agent, next)}
                    toggleLabel={`${s.allowSwitch} · ${agent.name}`}
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
                    title={reason}
                    type="button"
                    variant="secondary"
                  >
                    <Plus className="size-4" />
                  </Button>
                )
              }
              below={
                /* 只留「点开加号之后的那句引导」——那是这颗控件的内容，不是状态句。
                   机器细节、原因与用量都在悬停里（`agentTip`）。 */
                guide === agent.name ? (
                  <p className={CAPTION} role="note">
                    {!agent.installed
                      ? s.installGuide
                      : agent.logged_in.value === false
                        ? s.loginGuide
                        : s.loginUnknown}
                  </p>
                ) : null
              }
              description={s.intro[agent.name] ?? s.introFallback}
              icon={<OffloadVendorAvatar name={agent.name} />}
              key={agent.name}
              title={
                <span className="block truncate" title={agentTip(agent)}>
                  {vendorLabelFor(agent.name)}
                </span>
              }
            />
          )
        })}
      </SettingsGroup>
    </SettingsContent>
  )
}
