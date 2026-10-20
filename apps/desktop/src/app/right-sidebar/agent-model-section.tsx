import { useCallback, useEffect, useState } from 'react'

import { putAgentModel } from '@/app/console/api'
import { refreshConsoleAgents } from '@/app/console/store/agents'
import { canShowFollowDefault, pickerOverrides, type RosterAgent, splitQualified } from '@/app/settings/agent-model-overrides'
import { type AgentModelChoice, AgentModelPicker, FOLLOW_DEFAULT } from '@/app/settings/agent-model-picker'
import { useI18n } from '@/i18n'
import { requestModelOptions } from '@/lib/model-options'
import { notify, notifyError } from '@/store/notifications'
import type { ModelOptionProvider } from '@/types/plobi'

/**
 * 分身视图里那一段「这一个 Agent 用哪个模型」（WP-AGENT-MODEL-PICKER-MOUNT 第三档）。
 *
 * 挂在 Agent 自己的右栏，是因为这件事按用户拍的形状属于**那一个 Agent**：设置里配一次默认大家用，
 * 这里只往名册那一行写覆盖，空＝跟随默认（裁定 166 决定二／裁定 172）。目录不在这里另立一份真源，
 * 吃 `model.options`，与设置页同一份。
 *
 * 一条不许商量的形状：后端没回「这行有没有覆盖」那颗标记时（装机那份与 10-09 那个打包壳就是不返），
 * 这里**什么都不断**，只说"这份后端没说"——把"读不到"显示成"跟随默认"就是替用户撒谎。
 */

export function AgentModelSection({ agent }: { agent: RosterAgent }) {
  const { t } = useI18n()
  const r = t.rightSidebar
  const [providers, setProviders] = useState<readonly ModelOptionProvider[]>([])

  useEffect(() => {
    let live = true

    void requestModelOptions({})
      .then(result => {
        if (live) {
          setProviders(result.providers ?? [])
        }
      })
      .catch(() => {
        /* 目录读不到时下拉只剩「跟随默认」那颗；模型列表不是这一段的关键路径，不额外报错。 */
      })

    return () => {
      live = false
    }
  }, [])

  const pick = useCallback(async (choice: AgentModelChoice) => {
    // 后端按 profile_name 找那一行，而 profile_name 就是 `profile or name` ⇒ 缺 profile 时退回 id
    // 不是猜，是同一份解析规则（AgentEntry.profile_name）。
    const profileName = agent.profile || agent.id

    if (choice.model === null || choice.model === FOLLOW_DEFAULT) {
      // 撤回＝两个键都显式给空串（WP-MODEL-OVERRIDE-CLEAR 落的语义）。
      return { assignment: null as null | { model: string; provider: string }, profileName }
    }

    const parts = splitQualified(choice.model)

    return parts ? { assignment: parts, profileName } : null
  }, [agent.id, agent.profile])

  const onSave = useCallback(async (choice: AgentModelChoice) => {
    const intent = await pick(choice)

    if (!intent) {
      notifyError(new Error(r.modelStateUnknown), r.capabilitiesModel)

      return
    }

    try {
      await putAgentModel(intent.profileName, intent.assignment)
      // 服务端是唯一说了算的一方：写完重读那一行，界面不乐观猜自己刚写了什么。
      await refreshConsoleAgents()
      notify({ message: r.modelSaved })
    } catch (cause) {
      notifyError(cause, r.capabilitiesModel)
    }
  }, [pick, r.capabilitiesModel, r.modelSaved, r.modelStateUnknown])

  if (!canShowFollowDefault([agent])) {
    return (
      <p className="px-2 text-[0.7rem] leading-snug text-(--ui-text-quaternary)" data-testid="model-state-unknown">
        {r.modelStateUnknown}
        {agent.model ? ` · ${agent.model}` : ''}
      </p>
    )
  }

  return (
    <div className="px-2">
      <AgentModelPicker
        agents={[agent]}
        copy={{
          followDefault: r.modelFollowDefault,
          following: (model: string) => r.modelFollowing(model)
        }}
        onPick={choice => void onSave(choice)}
        overrides={pickerOverrides([agent])}
        providers={providers}
      />
    </div>
  )
}
