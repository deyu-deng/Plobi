/**
 * 左下底栏那一枚图标点出来的面板（用户 11-01：「设置里的 VPN 只是导入节点和配置规则的界面，
 * 控制应该在软件左侧底栏加一个图标，点击出现一个 overlay」）。
 *
 * 两屏，形状照 ikuuu 那两张截图，但只放控件：
 *  · 控制屏 —— 一枚电源键（整机走不走 Plobi）、规则/全局、TUN/系统代理；
 *  · 节点屏 —— 卡片式选节点，点一张就换过去。
 *
 * 三条纪律：
 *  · 读数一律来自 `GET /api/network/status` 的 `switch` / `tun` 两格，面板不自己推
 *    「现在是谁在管」（裁定 94）；
 *  · 屏上不解释机制：守卫为什么拒、缺哪个驱动、注册表写了什么，全在 tooltip 与日志里，
 *    屏上只有一枚警告图标（用户 10-27 令「不要乱写文案」）；
 *  · 没读到表之前不许点——空草稿覆盖整张表在这台掉过一次（46 成员/17 组/3 规则），
 *    这条 `loaded` 门和设置页同一个判据。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  asEgressBlock,
  exitOf,
  modeOf,
  patchExit,
  patchMode,
  patchTransport,
  proxyTooltip,
  transportOf,
  type EgressBlock,
  type EgressStatusExtra,
  type EgressSwitch,
  type EgressTun,
  type RuleMode,
  type Transport
} from '@/app/settings/egress-edit'
import { useI18n } from '@/i18n'
import { AlertTriangle, Check, Globe, LayoutGrid, Loader2, Power, Route } from '@/lib/icons'
import { getNetworkEgress, getNetworkStatus, saveNetworkEgress, setNetworkSystemProxy } from '@/plobi'
import type { StatusbarItem } from '@/app/shell/statusbar-controls'
import type { NetworkEgressRecord, NetworkStatus } from '@/types/plobi'

const CAPTION = 'text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)'

function Segmented<T extends string>({
  disabled,
  onChange,
  options,
  value
}: {
  disabled?: boolean
  onChange: (next: T) => void
  options: { label: string; value: T }[]
  value: T | ''
}) {
  return (
    <div className="flex shrink-0 overflow-hidden rounded-lg border border-(--stroke-nous) text-sm">
      {options.map(option => (
        <button
          aria-pressed={value === option.value}
          className={`flex-1 px-2.5 py-1.5 transition-colors disabled:cursor-default disabled:opacity-40 ${
            value === option.value ? 'bg-(--theme-primary) text-white' : 'text-(--ui-text-secondary)'
          }`}
          disabled={disabled}
          key={option.value}
          onClick={() => onChange(option.value)}
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function TabButton({
  active,
  children,
  icon: Icon,
  onClick
}: {
  active: boolean
  children: React.ReactNode
  icon: ({ className }: { className?: string }) => React.ReactNode
  onClick: () => void
}) {
  return (
    <button
      aria-pressed={active}
      className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs ${
        active ? 'bg-(--ui-control-hover-background) text-foreground' : 'text-(--ui-text-tertiary)'
      }`}
      onClick={onClick}
      type="button"
    >
      <Icon className="size-3.5" />
      {children}
    </button>
  )
}

export function EgressPanel() {
  const copy = useI18n().t.settings.network
  const [record, setRecord] = useState<NetworkEgressRecord | null>(null)
  const [status, setStatus] = useState<(EgressStatusExtra & NetworkStatus) | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  //: 拨不过去的那一次：留着它的理由与目标，屏幕才可能给出那一个出口（与别人的网卡并行）。
  const [refused, setRefused] = useState<{ foreign: boolean; target: Transport; why: string } | null>(null)
  const [view, setView] = useState<'nodes' | 'switch'>('switch')
  const [filter, setFilter] = useState('')

  const load = useCallback(async () => {
    const [nextRecord, nextStatus] = await Promise.all([getNetworkEgress(), getNetworkStatus()])
    setRecord(nextRecord)
    setStatus(nextStatus as EgressStatusExtra & NetworkStatus)
    setLoaded(true)
  }, [])

  useEffect(() => {
    load().catch((cause: unknown) => setError(String(cause)))
  }, [load])

  const block = useMemo(() => asEgressBlock(record?.network?.egress), [record])
  //: 后端那份读数是唯一真源；它少给一格时退到**同一模块**的算法，而不是在这里再写一份判断。
  const ctl: EgressSwitch = status?.switch ?? {
    exit: exitOf(block),
    mode: modeOf(block),
    transport: transportOf(block),
    vpn: transportOf(block) !== 'off'
  }
  const tun: EgressTun = status?.tun ?? {}
  const engineOk = status?.engine?.state === 'running-ours'
  const nodes = status?.membership?.nodes ?? Object.keys(block.nodes ?? {})
  const groups = status?.membership?.groups ?? {}
  const groupNames = Object.keys(groups)
  const shown = useMemo(() => (filter ? nodes.filter(name => (groups[filter] ?? []).includes(name)) : nodes), [filter, groups, nodes])

  /** 注册表那一半单独一次调用：它拒绝时那句话不许只进日志——表的声明与机器的事实是两份读数。 */
  const moveProxy = useCallback(async (enabled: boolean) => {
    const moved = await setNetworkSystemProxy(enabled)
    if (moved.proxy && moved.proxy.state === 'refused') setError(moved.proxy.detail ?? '')
    return moved
  }, [])

  /** 一次动作 = 改这一段 → 整段存回去（保存面是替换语义）→ 重新读那份唯一读数。 */
  const apply = useCallback(
    async (next: EgressBlock, proxy?: boolean) => {
      setBusy(true)
      setError('')
      try {
        const answer = await saveNetworkEgress({ egress: next })
        // 引擎那句话不许吞：这一屏存在的理由就是「配了不生效没人说」。
        if (answer.engine?.state === 'failed') setError(answer.engine.detail ?? '')
        if (proxy !== undefined) await moveProxy(proxy)
        await load()
      } catch (cause: unknown) {
        // 被拒是解析器在说话，不是传输坏了——它那句话有用，就摆在这。
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setBusy(false)
      }
    },
    [load, moveProxy]
  )

  /**
   * 拨载体单独一条路：**引擎拒了就退回原样**。
   *
   * 为什么不能像别的编辑那样"写进去等着"：表里写着 TUN 而机器上什么都没发生，正是这台
   * 10-29 那次事故的形状（面板写空表 ⇒ 引擎停了 ⇒ 注册表还指着那个死端口 ⇒ 整机没网）。
   * 守卫拒 TUN 的理由（不是管理员 / 场上有别人的网卡 / 缺驱动）都不该由屏幕替它乐观。
   */
  const flip = useCallback(
    async (target: Transport, allowForeign = false) => {
      const prev = transportOf(block)
      setBusy(true)
      setError('')
      setRefused(null)
      try {
        const next = patchTransport(block, target, { ...(tun.block ?? {}), 'allow-foreign': allowForeign })
        const answer = await saveNetworkEgress({ egress: next })
        if (answer.engine?.state === 'failed') {
          const why = answer.engine.detail ?? '引擎没起来'
          await saveNetworkEgress({ egress: block })
          await moveProxy(prev === 'system')
          setRefused({ foreign: /allow-foreign|别人的/.test(why), target, why })
          setError(why)
        } else if (target !== prev) {
          // TUN 与系统代理互斥：拨一个就把另一个的注册表那一半带走/带回来。
          // 它拒绝照样要说——那是这半截事实自己的答案，不是引擎那一半的。
          await moveProxy(target === 'system')
        }
        await load()
      } catch (cause: unknown) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setBusy(false)
      }
    },
    [block, load, moveProxy, tun.block]
  )

  /** 电源键：开着就整机交还；关掉时回到表里已经配过的那个载体（配过 TUN 就回 TUN）。 */
  const power = () => {
    if (ctl.vpn) {
      void flip('off')
      return
    }
    const configured = Boolean(block.engine?.tun?.name) && Boolean(block.engine?.tun?.stack)
    void flip(configured ? 'tun' : 'system')
  }

  const tunBlocked = tun.requested !== true && tun.ok === false
  //: 「表说要接管」≠「注册表是我们的」。这台 10-31 实测：别的程序会在我们之后自己改回去，
  //: 而开关照旧亮着。形状说这一句（一枚警告），四句话的区别留在 tooltip。
  const proxyWarn = ctl.transport === 'system' && status?.system_proxy !== undefined && status.system_proxy.ours === false

  return (
    <div className="w-80 select-text" data-testid="egress-panel">
      <div className="flex items-center gap-1 border-b border-(--ui-stroke-tertiary) px-2 py-1.5">
        <TabButton active={view === 'switch'} icon={Power} onClick={() => setView('switch')}>
          {copy.tabSwitch}
        </TabButton>
        <TabButton active={view === 'nodes'} icon={LayoutGrid} onClick={() => setView('nodes')}>
          {copy.tabNodes}
        </TabButton>
        {busy ? <Loader2 className="ml-auto size-3.5 animate-spin text-(--ui-text-tertiary)" /> : null}
        {!busy && !loaded ? <AlertTriangle className="ml-auto size-3.5 text-(--ui-text-danger)" title={copy.readFailed} /> : null}
      </div>

      {view === 'switch' ? (
        <div className="grid gap-2.5 px-3 py-3">
          <div className="flex items-center gap-3">
            <button
              aria-label={ctl.vpn ? copy.powerOff : copy.powerOn}
              className={`grid size-14 shrink-0 place-items-center rounded-full border-2 transition-colors ${
                ctl.vpn
                  ? 'border-(--theme-primary) bg-(--theme-primary) text-white'
                  : 'border-(--stroke-nous) text-(--ui-text-tertiary)'
              }`}
              disabled={busy || !loaded}
              onClick={power}
              type="button"
            >
              <Globe className="size-6" />
            </button>
            <div className="grid min-w-0 gap-0.5">
              <div className="flex min-w-0 items-center gap-1.5">
                <StatusDot ok={engineOk} title={status?.engine?.detail ?? copy.engineNotRead} />
                <span className="truncate text-sm font-medium" title={ctl.exit || copy.direct}>
                  {ctl.exit || copy.direct}
                </span>
              </div>
              <span className={CAPTION} title={tun.reasons?.[0]}>
                {ctl.transport === 'tun' ? copy.transportTun : ctl.transport === 'system' ? copy.transportSystem : copy.transportOff}
              </span>
            </div>
          </div>

          <Segmented<RuleMode>
            disabled={busy || !loaded}
            onChange={mode => void apply(patchMode(block, mode))}
            options={[
              { label: copy.modeRule, value: 'rule' },
              { label: copy.modeGlobal, value: 'global' }
            ]}
            value={ctl.mode}
          />

          <div className="flex items-center gap-1.5">
            <Segmented<Transport>
              disabled={busy || !loaded}
              onChange={transport => void flip(transport)}
              options={[
                { label: copy.transportTun, value: 'tun' },
                { label: copy.transportSystem, value: 'system' }
              ]}
              value={ctl.transport}
            />
            {tunBlocked ? (
              <span
                className="grid size-6 shrink-0 place-items-center rounded-md text-(--ui-text-danger)"
                data-testid="tun-warning"
                title={tun.reasons?.join('\n')}
              >
                <AlertTriangle className="size-3.5" />
              </span>
            ) : null}
            {proxyWarn ? (
              <span
                className="grid size-6 shrink-0 place-items-center rounded-md text-(--ui-text-danger)"
                data-testid="proxy-warning"
                title={proxyTooltip(copy, status?.system_proxy)}
              >
                <AlertTriangle className="size-3.5" />
              </span>
            ) : null}
          </div>

          {error ? <p className="text-xs text-(--ui-text-danger)">{error}</p> : null}
          {refused?.foreign ? (
            // 纸 §25 买的那条出口：默认拒绝与别人的网卡抢默认路由，但这是**表能说的一个决定**，
            // 所以拒了之后屏幕给一个按钮，而不是只给一句话。
            <button
              className="justify-self-start rounded-lg border border-(--stroke-nous) px-2.5 py-1.5 text-xs text-(--ui-text-secondary)"
              disabled={busy}
              onClick={() => void flip(refused.target, true)}
              type="button"
            >
              {copy.allowForeignRetry}
            </button>
          ) : null}
        </div>
      ) : (
        <div className="grid gap-2 px-3 py-3">
          {groupNames.length > 0 ? (
            <div className="flex gap-1.5 overflow-x-auto pb-1 [scrollbar-width:none]">
              <button
                className={`shrink-0 rounded-full border border-(--stroke-nous) px-2.5 py-1 text-xs ${
                  filter === '' ? 'bg-(--theme-primary) text-white' : 'text-(--ui-text-secondary)'
                }`}
                onClick={() => setFilter('')}
                type="button"
              >
                {copy.panelAll}
              </button>
              {groupNames.map(name => (
                <button
                  className={`shrink-0 rounded-full border border-(--stroke-nous) px-2.5 py-1 text-xs ${
                    filter === name ? 'bg-(--theme-primary) text-white' : 'text-(--ui-text-secondary)'
                  }`}
                  key={name}
                  onClick={() => setFilter(name)}
                  type="button"
                >
                  {name}
                </button>
              ))}
            </div>
          ) : null}

          <div className="grid max-h-72 grid-cols-2 gap-1.5 overflow-y-auto">
            {shown.map(name => (
              <button
                className={`flex min-h-11 items-center gap-1.5 rounded-lg border border-(--stroke-nous) px-2 py-1.5 text-left text-xs ${
                  ctl.exit === name ? 'bg-(--theme-primary) text-white' : 'text-(--ui-text-secondary)'
                }`}
                disabled={busy || !loaded}
                key={name}
                onClick={() => void apply(patchExit(block, name))}
                title={name}
                type="button"
              >
                {ctl.exit === name
                  ? <Check className="size-3 shrink-0" />
                  : <Route className="size-3 shrink-0 opacity-60" />}
                {/* 换行到两行，不截断：节点名就是被选中的东西，看不到等于没得选（用户 11-01「文字被遮挡」）。 */}
                <span className="break-words [display:-webkit-box] [overflow-wrap:anywhere] [webkit-box-orient:vertical] [webkit-line-clamp:2]">
                  {name}
                </span>
              </button>
            ))}
            {shown.length === 0 ? <p className={CAPTION}>{copy.panelEmpty}</p> : null}
          </div>
        </div>
      )}
    </div>
  )
}

/** 状态用形状说：一个点，绿=引擎正带着流量，红=没在带（用户 10-27：别用句子表达状态）。 */
function StatusDot({ ok, title }: { ok: boolean; title: string }) {
  return (
    <span
      aria-hidden="true"
      className={`size-2 shrink-0 rounded-full ${ok ? 'bg-(--theme-primary)' : 'bg-(--ui-text-danger)'}`}
      title={title}
    />
  )
}

/** 底栏那一枚图标自己管颜色：整机被接管着就亮，没接管就灰。
 *  读的是同一格 `status.switch`，面板开着时它也跟着变——一事实一读数（裁定 94）。 */
export function EgressRailIcon({ className }: { className?: string }) {
  const [vpn, setVpn] = useState(false)

  useEffect(() => {
    let alive = true
    const read = () => {
      getNetworkStatus()
        .then(next => {
          if (alive) setVpn(Boolean((next as EgressStatusExtra).switch?.vpn))
        })
        .catch(() => {})
    }
    read()
    const timer = setInterval(read, 60_000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  return <Globe className={`${className ?? ''} ${vpn ? 'text-(--theme-primary)' : ''} size-3`} />
}

/**
 * 底栏那一格。放在面板自己这一档、由 `useStatusbarItems` 直接摆出去：控制面归这一族，
 * 状态栏只是它挂的地方（被占文件只留一行挂载那条规矩的另一半——逻辑不许散进别人那一档）。
 *
 * 文案硬编中文：i18n 那五档在别的线手上（裁定 77 拦过一次），落那儿的窗口到了再搬。
 */
export function egressRailItem(title: string): StatusbarItem {
  return {
    icon: <EgressRailIcon />,
    id: 'egress',
    menuClassName: 'w-80',
    menuContent: () => <EgressPanel />,
    title,
    variant: 'menu'
  }
}
