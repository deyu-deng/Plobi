/**
 * 设置 → 外包：一家外部软件一行。
 *
 * **它与 Providers / 模型那一屏不是一件事**：那边把订阅/API 借来当模型；这里管的是
 * 「把一段活交给那家软件、让它在自己界面里干」——一个人、一趟活、一条它自己的会话。
 *
 * 这一屏的形状（10-23 与用户对齐）：**只读、说真话**。
 * - 能不能用由后端一处判据给（`availability()`），这里不自己判第二遍；
 * - 登录态写清**来源**（上一趟真跑的证据 / 没证据），存在性猜的那种只当提示；
 * - 没有可点的假按钮：回跳能力有就写「可以回到它那屏」，没有就写没有。
 */

import { useCallback, useEffect, useState } from 'react'

import { OffloadVendorAvatar } from '@/app/chat/sidebar/offload-vendor-icon'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Package, RefreshCw } from '@/lib/icons'

import { getOffloadAgents, type OffloadAgent, type OffloadPayload } from './offload/api'
import { ListRow, LoadingState, Pill, SectionHeading, SettingsContent } from './primitives'

const CAPTION = 'text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)'
const MONO = 'font-mono text-[0.68rem] text-(--ui-text-tertiary)'

function shortTime(iso: string): string {
  // 后端给的是 UTC ISO；只做"给人看"的截取，不做时区换算（那是显示器的事）。
  return iso ? iso.replace('T', ' ').replace('+00:00', 'Z').slice(0, 16) : ''
}

function money(value: number | null, status: string): string {
  if (value === null || value === undefined) {
    return status === 'no_usage_reported' ? '—' : '—'
  }
  return `$${value.toFixed(2)}`
}

export function OffloadSettings() {
  const { t } = useI18n()
  const s = t.settings.offload
  const [payload, setPayload] = useState<OffloadPayload | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (refresh: boolean) => {
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
    void load(false)
  }, [load])

  const loginText = (agent: OffloadAgent): string => {
    if (agent.logged_in.value === true) return s.loginYes
    if (agent.logged_in.value === false) return s.loginNo
    return s.loginUnknown
  }

  return (
    <SettingsContent>
      <SectionHeading
        icon={Package}
        meta={payload ? `${s.probedAt} ${shortTime(payload.probed_at)}` : undefined}
        title={s.title}
      />
      <p className={`${CAPTION} pb-1`}>{s.note}</p>
      <div className="pb-2">
        <Button disabled={busy} onClick={() => void load(true)} size="sm" variant="ghost">
          <RefreshCw className={busy ? 'animate-spin' : ''} />
          {s.refresh}
        </Button>
      </div>
      {error ? <p className="pb-2 text-sm text-(--ui-text-danger)">{error}</p> : null}
      {!payload && !error ? <LoadingState label={s.loading} /> : null}
      {payload?.agents.map(agent => (
        <ListRow
          key={agent.name}
          action={
            <Pill tone={agent.can_use ? 'primary' : 'muted'}>
              {agent.can_use ? s.usable : s.blocked}
            </Pill>
          }
          below={
            <div className="mt-2 space-y-1">
              {agent.why_not ? <div className={CAPTION}>{agent.why_not}</div> : null}
              <div className={CAPTION}>
                {s.login}: {loginText(agent)}
                {agent.logged_in.value === null && agent.login_hint ? ` · ${s.hintSaysMaybe}` : ''}
              </div>
              <div className={CAPTION}>
                {s.today}: {agent.today.rounds} · {money(agent.today.total_usd, agent.today.cost_status)}
                {agent.today.last_ts ? ` · ${s.lastAt} ${shortTime(agent.today.last_ts)}` : ''}
              </div>
              {agent.last_round?.answer_excerpt ? (
                <div className={CAPTION}>
                  {s.itSaid}: {agent.last_round.answer_excerpt}
                </div>
              ) : null}
              <div className={CAPTION}>
                {agent.resume_supported ? s.resumeYes : s.resumeNo}
                {agent.last_round?.external_session ? ` · ${s.sessionId} ${agent.last_round.external_session}` : ''}
              </div>
            </div>
          }
          description={agent.installed ? agent.binary : s.notInstalled}
          hint={agent.version ? `${agent.name} ${agent.version}` : undefined}
          title={
            <span className="flex items-center gap-2">
              <OffloadVendorAvatar vendor={agent.name} />
              <span>{agent.name}</span>
              <span className={MONO}>{agent.permission_flag}</span>
            </span>
          }
        />
      ))}
    </SettingsContent>
  )
}
