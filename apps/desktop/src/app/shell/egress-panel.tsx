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
    //: `min-w-0` 与 `w-full` 不是装饰：这两颗按钮里是**四种语言**的标签（en 的
    //: 「System proxy」、ja 的「システムプロキシ」），而这里以前是 `shrink-0` ⇒ 整段
    //: 按内容撑宽、顶出弹窗被裁掉（用户 11-01 截图：右边「Everything」只剩「Eve」）。
    //: 段宽由外框给，标签不换行，超出才省略——宁可省略也不许把面板撑破。
    <div className="flex w-full min-w-0 overflow-hidden rounded-lg border border-(--stroke-nous) text-sm">
      {options.map(option => (
        <button
          aria-pressed={value === option.value}
          className={`min-w-0 flex-1 truncate whitespace-nowrap px-2 py-1.5 transition-colors disabled:cursor-default disabled:opacity-40 ${
            value === option.value ? 'bg-(--theme-primary) text-white' : 'text-(--ui-text-secondary)'
          }`}
          disabled={disabled}
          key={option.value}
          onClick={() => onChange(option.value)}
          title={option.label}
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
  const [refused, setRefused] = useState<{ foreign: boolean; strict: boolean; target: Transport; why: string } | null>(null)
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
  /**
   * 筛选片只放**真的分过类**的组。
   *
   * 订阅带来的组有两种：一种是兜底（`POOL`，46/46，成员＝全部节点），一种才是分类
   * （日本 18 / 香港 11 / 新加坡 3 …）。兜底那种与上面那颗「全部」是同一个过滤器，
   * 摆两遍只会让这一行看起来没分过类；而按表里的原始顺序（乌克兰 1、俄罗斯 1、
   * 加拿大 1…）排在前面，屏幕上就先看到一串只有一颗节点的地区 —— 用户 11-01 第三次
   * 看这个浮窗就是这么说的：「分类没分好」。所以：滤掉兜底、只留**真能筛出东西**的
   * 组（≥2 个成员）、按成员数从多到少排。一颗节点的地区不是分类，是那颗节点本身。
   */
  const groupNames = useMemo(() => {
    const all = new Set(nodes)
    return Object.keys(groups)
      .filter(name => {
        const members = groups[name] ?? []
        const catchAll = members.length === all.size && members.every(member => all.has(member))

        return !catchAll && members.length >= 2
      })
      .sort((a, b) => (groups[b]?.length ?? 0) - (groups[a]?.length ?? 0) || a.localeCompare(b))
  }, [groups, nodes])

  /**
   * 卡片按**所在的最小组**（＝地区）聚在一起，组的大小从多到少。
   *
   * 订阅给的顺序是「香港W01 / 日本W01 / 香港W02 / 日本W02…」，照它排，两列时每一行
   * 都混着两个地区（用户 11-01 的截图就是那样）；三列也只是碰巧。聚起来之后，同一片
   * 地区连成一段，读法与上面那排筛选片一致。
   */
  const ordered = useMemo(() => {
    const size = new Map<string, number>()
    nodes.forEach(name => {
      let best = Number.POSITIVE_INFINITY
      groupNames.forEach(group => {
        const members = groups[group] ?? []
        if (members.includes(name) && members.length < best) best = members.length
      })
      //: 不在任何筛选组里的节点（只属于自己的那一颗）排到最后：`Infinity` 不能直接拿去
      //: 相减 —— `Infinity - Infinity` 是 `NaN`，比较器返回 `NaN` 等于没有排序。
      size.set(name, best === Number.POSITIVE_INFINITY ? -1 : best)
    })
    return [...nodes].sort((a, b) => (size.get(b) ?? -1) - (size.get(a) ?? -1) || a.localeCompare(b))
  }, [groupNames, groups, nodes])

  const shown = useMemo(
    () => (filter ? ordered.filter(name => (groups[filter] ?? []).includes(name)) : ordered),
    [filter, groups, ordered]
  )

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
    async (target: Transport, opts: { allowForeign?: boolean; strictRoute?: boolean } = {}) => {
      const prev = transportOf(block)
      setBusy(true)
      setError('')
      setRefused(null)
      try {
        const next = patchTransport(block, target, {
          ...(tun.block ?? {}),
          ...(opts.allowForeign ? { 'allow-foreign': true } : {}),
          ...(opts.strictRoute ? { 'strict-route': true } : {})
        })
        const answer = await saveNetworkEgress({ egress: next })
        if (answer.engine?.state === 'failed') {
          const why = answer.engine.detail ?? '引擎没起来'
          await saveNetworkEgress({ egress: block })
          await moveProxy(prev === 'system')
          // 两句拒因要分得开：并行别人的网卡与强制接管默认路由是两个不同的决定（一个网卡，一张路由表）。
          setRefused({ foreign: /allow-foreign/.test(why), strict: /strict-route/.test(why), target, why })
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
    //: 宽度由这一格按屏给（弹窗那格是 `w-auto`，跟着这里走）：控制屏是一列控件，
    //: 22rem 够；节点屏是卡片，要**一行三张**才不显得空（用户 11-01：「怎么也要一横行
    //: 能同时显示三个卡片的宽度」）⇒ 32rem，三列各 ~158px，名字两行放得下。
    //: 高度也要有界：这一屏挂在底栏弹窗里，弹窗的可视高度由视口给，内容顶出去就是**下半截看不见**
    //: （用户 11-05 那张截图正是切到一半）。列布局＋正文自己滚，表头那一行始终在。
    <div
      className={`flex max-h-[min(70vh,34rem)] min-w-0 flex-col select-text ${view === 'nodes' ? 'w-[32rem]' : 'w-96'}`}
      data-testid="egress-panel"
    >
      <div className="flex shrink-0 items-center gap-1 border-b border-(--ui-stroke-tertiary) px-2 py-1.5">
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
        //: `grid-cols-1` = `minmax(0,1fr)`：裸 `grid` 的隐式轨道按 max-content 算，
        //: 一行长标签就能把整列顶宽、然后被弹窗裁掉（11-01 那次截图的成因）。
        <div className="grid min-w-0 grid-cols-1 gap-2.5 overflow-y-auto px-3 py-3">
          <div className="flex min-w-0 items-center gap-3">
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
            <div className="grid min-w-0 grid-cols-1 gap-0.5">
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

          <div className="flex min-w-0 items-center gap-1.5">
            <div className="min-w-0 flex-1">
              <Segmented<Transport>
                disabled={busy || !loaded}
                onChange={transport => void flip(transport)}
                options={[
                  { label: copy.transportTun, value: 'tun' },
                  { label: copy.transportSystem, value: 'system' }
                ]}
                value={ctl.transport}
              />
            </div>
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

          {error ? (
            //: 屏上那一行只说"这件事没成"，机器原话留在悬停里（10-27 文案纪律：机器词不许上屏）。
            //: 11-05 这台的现场是 `Error invoking remote method 'plobi:api': Invalid character in
            //: header content [...]` ——一整句内部机制顶在开关下面，还把这一屏往下挤出弹窗的可见高度。
            <p className="line-clamp-1 text-xs text-(--ui-text-danger)" title={error}>
              {/invoking remote method|ERR_[A-Z_]+|Invalid character|\b(TypeError|ReferenceError|RangeError)\b/.test(error)
                ? copy.refused
                : error}
            </p>
          ) : null}
          {refused ? (
            // 纸 §25 与 11-02 那条：默认都拒，但拒了要给**那一个**决定——并行别人的网卡、
            // 和把默认路由从别人那里抢过来，是两件事，不是一颗按钮。
            <div className="flex flex-wrap gap-1.5">
              {refused.foreign ? (
                <button
                  className="max-w-full rounded-lg border border-(--stroke-nous) px-2.5 py-1.5 text-left text-xs text-(--ui-text-secondary)"
                  disabled={busy}
                  onClick={() => void flip(refused.target, { allowForeign: true })}
                  type="button"
                >
                  {copy.allowForeignRetry}
                </button>
              ) : null}
              {refused.strict ? (
                <button
                  className="max-w-full rounded-lg border border-(--stroke-nous) px-2.5 py-1.5 text-left text-xs text-(--ui-text-secondary)"
                  disabled={busy}
                  onClick={() => void flip(refused.target, { allowForeign: true, strictRoute: true })}
                  type="button"
                >
                  {copy.strictRouteRetry}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="grid min-w-0 grid-cols-1 gap-2 px-3 py-3">
          {groupNames.length > 0 ? (
            //: 换行而不是横向滚动：横向滚 + 藏滚动条时，最后一颗被面板边缘切一半，
            //: 与「被裁了」长得一模一样（用户 11-01 已经为这个形态提过两次）。
            <div className="flex flex-wrap gap-1.5">
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

          <div className="grid max-h-80 grid-cols-3 gap-1.5 overflow-y-auto">
            {shown.map(name => (
              <button
                className={`flex min-h-10 items-center gap-1 rounded-lg border border-(--stroke-nous) px-1.5 py-1 text-left text-xs ${
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
    //: `window.plobiDesktop` 缺席时 `getNetworkStatus()` 是**同步抛**的（读 `.api` 就炸）。
    //: 这个 effect 在 `useEffect` 里跑，同步抛会直接把整棵树上进错误边界——所以这里必须
    //: 是 async：async 函数里的同步抛会变成 rejected promise，下面的 `.catch` 才接得住。
    //: （11-01 这台实测：一次 `vite build` 把开发态的 `dist/electron-preload.js` 抹掉，
    //: 桥没了，底栏这枚图标就把整个界面崩成「Something went wrong」。）
    const read = async () => {
      try {
        const next = await getNetworkStatus()
        if (alive) setVpn(Boolean((next as EgressStatusExtra).switch?.vpn))
      } catch {
        // 读不到就保持灰色：图标是状态的渲染，不是状态的来源。
      }
    }
    void read()
    const timer = setInterval(() => void read(), 60_000)
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
 * 文案走 `useI18n().t.settings.network`（11-01 四语目录接回屏幕；`egressRailItem` 的
 * `title` 由挂载方给，它自己不带句子）。
 */
export function egressRailItem(title: string): StatusbarItem {
  return {
    icon: <EgressRailIcon />,
    id: 'egress',
    //: 宽度**不在这里定**：两屏要的宽度不一样（控制 22rem / 节点 30rem，见面板根那一格），
    //: 所以这里只给 `w-auto` —— 弹窗跟着内容走，宽度只有一个来源，也就不会再出现
    //: 「两处都写 320，里面一撑宽就被裁」（11-01 那次截图的成因）。
    menuClassName: 'w-auto',
    menuContent: () => <EgressPanel />,
    title,
    variant: 'menu'
  }
}
