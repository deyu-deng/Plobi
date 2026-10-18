import type { Agent } from '@/app/console/types'
import type { ModelOptionProvider } from '@/types/plobi'

/**
 * Per-分身 model dropdown (裁定 168 / WP-AGENT-MODEL-PICKER).
 *
 * The shape this component exists to express: one shared default (the single
 * save in Settings) plus **one override per roster row**, where "no override"
 * is a real, selectable value — not an empty box. That's why `FOLLOW_DEFAULT`
 * is the empty string and the first option: clearing the override is the
 * common action, and the UI must be able to say "put it back".
 *
 * Two deliberate seams, because the other halves aren't this file's to write:
 *
 * * `overrides` is passed in rather than read off `Agent`. The roster row's
 *   effective model (`agent.model`) is route-resolved, so it cannot tell
 *   "following" from "explicitly set to the same model" — the backend has to
 *   surface the override flag first. Until then the caller owns that mapping.
 * * `copy` is injected. Renderer-facing strings go through the i18n catalog,
 *   and this component's caller wires them there.
 *
 * The option list comes from the same `model.options` result the Settings page
 * already fetched. Nothing here re-pulls a catalog.
 */

/** 空值＝「跟随默认」。清掉覆盖是常态动作，所以它必须是第一个、也是没覆盖时显示的那一颗。 */
export const FOLLOW_DEFAULT = ''

export interface AgentModelChoice {
  agentId: string
  /** `null` = 跟随默认（写回名册就是把那行的 `model:`/`provider:` 撤掉）。 */
  model: string | null
}

export interface AgentModelPickerProps {
  agents: readonly Agent[]
  /** 与设置页同一份 `model.options` 的返回；下拉不许自己再拉一遍目录。 */
  providers: readonly ModelOptionProvider[]
  /** 名册行上已写的覆盖（分身 id → `provider/model`）。缺这颗＝那一行跟随默认。 */
  overrides: Readonly<Record<string, string | undefined>>
  copy: {
    /** 「跟随默认」那颗选项的字面。 */
    followDefault: string
    /** 跟随默认时补一句现在跟的是谁，让「空」不等于「没读数」。 */
    following: (model: string) => string
  }
  onPick: (choice: AgentModelChoice) => void
}

/** 把 `model.options` 那一份摊平成 `provider/model` 列表（顺序照后端给的 provider 顺序）。 */
export function flattenModelOptions(providers: readonly ModelOptionProvider[]): string[] {
  const out: string[] = []

  for (const provider of providers) {
    for (const model of provider.models ?? []) {
      const qualified = `${provider.slug}/${model}`

      if (!out.includes(qualified)) {
        out.push(qualified)
      }
    }
  }

  return out
}

export function AgentModelPicker({
  agents,
  copy,
  onPick,
  overrides,
  providers
}: AgentModelPickerProps) {
  const options = flattenModelOptions(providers)

  return (
    <div className="flex flex-col gap-2">
      {agents.map(agent => {
        const override = overrides[agent.id]
        const value = override ?? FOLLOW_DEFAULT

        return (
          <label className="flex items-center justify-between gap-3 text-sm" data-agent-id={agent.id} key={agent.id}>
            <span className="truncate">{agent.name}</span>
            <span className="flex shrink-0 items-center gap-2">
              {value === FOLLOW_DEFAULT && agent.model ? (
                <span className="text-xs text-muted-foreground">{copy.following(agent.model)}</span>
              ) : null}
              <select
                onChange={event => {
                  const next = event.target.value

                  onPick({ agentId: agent.id, model: next === FOLLOW_DEFAULT ? null : next })
                }}
                value={value}
              >
                <option value={FOLLOW_DEFAULT}>{copy.followDefault}</option>
                {options.map(option => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </span>
          </label>
        )
      })}
    </div>
  )
}
