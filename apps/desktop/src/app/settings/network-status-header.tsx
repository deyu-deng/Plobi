import { useI18n } from '@/i18n'
import type { NetworkStatus } from '@/types/plobi'

/**
 * 「这套出口现在到底成没成」——设置页只**读**，不在这里放开关。
 *
 * 开关（一屏 VPN / 规则还是全局 / TUN 还是系统代理）住在底栏那块面板里（用户 11-01 令：
 * 控制只在面板，设置页只写表）。这里回答的是另一个问题：**我刚才配的东西生效了吗**。
 *
 * 读数全部来自 `/api/network/status` 的同一份快照，和面板、托盘、CLI 共用一次判定，
 * 因此这屏不可能长出自己的第二套说法（裁定 94）。
 *
 * 文案只用既有的 `t.settings.network` 键，不新增一个字：`proxyOurs` 那四句本来就是
 * 为这一格写的，此前只挂在底栏面板上，设置页反而没有——配完不知道成没成，是真实的坑。
 */
type Tone = 'ok' | 'warn' | 'danger' | 'muted'

const TONE_CLASS: Record<Tone, string> = {
  ok: 'bg-(--dl-status-ok-soft) text-(--dl-status-ok)',
  warn: 'bg-(--dl-status-warn-soft) text-(--dl-status-warn)',
  danger: 'bg-(--dl-status-danger-soft) text-(--dl-status-danger)',
  muted: 'bg-(--ui-bg-secondary) text-(--ui-text-tertiary)'
}

const LABEL = 'text-[length:var(--conversation-caption-font-size)] leading-(--dl-type-line-snug) text-(--ui-text-tertiary)'
const DETAIL = 'text-(length:--dl-type-size-xs) leading-(--dl-type-line-snug) text-(--ui-text-secondary)'

function Reading({ detail, label, short, tone }: { detail?: string; label: string; short: string; tone: Tone }) {
  return (
    <div className="min-w-0 rounded-[var(--dl-radius-md)] bg-(--dl-surface-sunken) px-3 py-2">
      <dt className={LABEL}>{label}</dt>
      <dd className="mt-1 min-w-0">
        <span
          className={`inline-flex max-w-full items-center rounded-full px-2 py-0.5 text-[length:var(--conversation-caption-font-size)] font-medium ${TONE_CLASS[tone]}`}
        >
          <span className="truncate">{short}</span>
        </span>
        {detail ? <p className={`mt-1 ${DETAIL}`}>{detail}</p> : null}
      </dd>
    </div>
  )
}

export function NetworkStatusHeader({
  ruleMatches,
  status
}: {
  /** 草稿里已填的规则（含还没保存的改动）——这一格说的是「表里写着什么」，不是「引擎里跑着什么」。 */
  ruleMatches: string[]
  status: NetworkStatus | null
}) {
  const { t } = useI18n()
  const copy = t.settings.network
  const engine = status?.engine
  const proxy = status?.system_proxy

  const engineReading = !engine?.enabled
    ? { detail: copy.engineNotRead, short: copy.powerOff, tone: 'muted' as Tone }
    : engine.mixed_port
      ? { short: `${copy.port} ${engine.mixed_port}`, tone: 'ok' as Tone }
      : { detail: copy.engineNotRead, short: copy.powerOn, tone: 'warn' as Tone }

  const proxyReading = !proxy
    ? { detail: '', short: copy.engineNotRead, tone: 'muted' as Tone }
    : !proxy.supported
      ? { detail: copy.proxyUnsupported, short: copy.powerOff, tone: 'muted' as Tone }
      : proxy.ours
        ? { detail: copy.proxyOurs, short: copy.powerOn, tone: 'ok' as Tone }
        : proxy.declared
          ? { detail: copy.proxyDeclaredNotApplied, short: copy.refused, tone: 'warn' as Tone }
          : { detail: copy.proxyNotOurs, short: copy.powerOff, tone: 'muted' as Tone }

  const filled = ruleMatches.filter((match) => match.trim())
  const hasCatchAll = filled.some((match) => match.trim() === '*')

  const ruleReading =
    filled.length === 0
      ? { detail: copy.catchAll, short: '0', tone: 'danger' as Tone }
      : hasCatchAll
        ? { short: String(filled.length), tone: 'ok' as Tone }
        : { detail: copy.catchAll, short: String(filled.length), tone: 'warn' as Tone }

  return (
    <dl className="grid gap-2 py-3 sm:grid-cols-3">
      <Reading detail={engineReading.detail} label={copy.engine} short={engineReading.short} tone={engineReading.tone} />
      <Reading detail={proxyReading.detail} label={copy.proxyApplied} short={proxyReading.short} tone={proxyReading.tone} />
      <Reading detail={ruleReading.detail} label={copy.rules} short={ruleReading.short} tone={ruleReading.tone} />
    </dl>
  )
}
