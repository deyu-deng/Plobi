import type { Agent } from '@/app/console/types'

/**
 * 「谁来跑这件事」那一格的算法（WP-CRON-RECIPIENT）。
 *
 * 选项**只取名册那一份**（`GET /api/agents` 的行），不读 profiles——分身家正在拆，
 * 那一套是旧形状，读它等于把一个正在消失的概念继续摆到屏幕上。
 *
 * 「不指定」不是字符串 `'default'`：它是**不给后端这个键**，让作业按创建处的身份跑（旧行为）。
 * 混成一个字符串，就会有一个并不存在的 Agent 出现在作业记录里。
 */

export const RECIPIENT_NONE = ''

export interface RecipientOption {
  id: string
  name: string
}

/** 名册顺序照后端给的原样，不重排：重排会让"上一个选的是谁"在两次打开之间跳。 */
export function recipientOptions(agents: readonly Agent[]): RecipientOption[] {
  const out: RecipientOption[] = []

  for (const agent of agents) {
    if (!agent?.id || out.some(option => option.id === agent.id)) {
      continue
    }

    out.push({ id: agent.id, name: agent.name || agent.id })
  }

  return out
}

/** 表单值 → 送进后端的 `recipient`。空＝这个键干脆不给。 */
export function recipientToPayload(value: string): string | undefined {
  const text = (value || '').trim()

  return text ? text : undefined
}

/**
 * 打开一份已有作业时回填。名册里查不到那个人（改名／归档／后端还没升级）时
 * **保留原值**，不许静默改成"不指定"——那会让用户以为作业没人跑，而实际上它一直写着某个名字。
 */
export function recipientInitialValue(jobRecipient: null | string | undefined): string {
  return (jobRecipient || '').trim() || RECIPIENT_NONE
}
