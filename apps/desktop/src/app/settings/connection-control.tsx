import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Loader2, Plus } from '@/lib/icons'

/**
 * 配置卡右侧那**一颗**控件（`WP-SETTINGS-CARD-UNIFY`；语法见 `Docs/specs/settings-card-system.md` §2）。
 *
 * 三形＋一种缺席：没配过 = `+`；配过 = 开关；等浏览器那一步回来 = spinner；没有任何可点动作 = 不画。
 * 坏了**不换控件**（灰控件是"你可以点但点了没用"，那是谎）：开关保持可点，旁边一颗红点，
 * 原因进 `errorTitle`（tooltip）与点开的抽屉——用户 11-07 拍「形状说话，坏了才红点」。
 *
 * 这套逻辑从前在 providers 与 sources 各写了一遍；行为（跳浏览器／拉进程／写黑名单）仍归各页，
 * 这里只统一"画哪一颗"。
 */
export function ConnectionControl({
  busy = false,
  checked = false,
  configureLabel,
  configureTestId,
  error = false,
  errorTitle,
  mode,
  onConfigure,
  onToggle,
  toggleLabel
}: {
  /** 请求在飞：此刻按不动。不用于"它坏了"。 */
  busy?: boolean
  checked?: boolean
  configureLabel: string
  /** 测试挂钩：没配过那一形给个稳定 testid（屏上不靠它）。 */
  configureTestId?: string
  error?: boolean
  errorTitle?: string
  mode: 'configure' | 'toggle' | 'pending' | 'none'
  onConfigure?: () => void
  onToggle?: (next: boolean) => void
  toggleLabel: string
}) {
  if (mode === 'none') {
    return null
  }

  if (mode === 'pending') {
    return (
      <span aria-label={configureLabel} className="inline-flex size-8 items-center justify-center" role="status">
        <Loader2 className="size-4 animate-spin text-(--ui-text-tertiary)" />
      </span>
    )
  }

  if (mode === 'configure') {
    return (
      <Button
        aria-label={configureLabel}
        data-testid={configureTestId}
        disabled={busy}
        onClick={onConfigure}
        size="icon-sm"
        variant="secondary"
      >
        <Plus className="size-4" />
      </Button>
    )
  }

  return (
    <span className="inline-flex items-center gap-2">
      {error
        ? <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-destructive" title={errorTitle} />
        : null}
      <Switch
        aria-label={toggleLabel}
        checked={checked}
        disabled={busy}
        onCheckedChange={onToggle}
      />
    </span>
  )
}
