export interface RestoreMessageTarget {
  text: string
  userOrdinal: number | null
}

/**
 * 挂 `Thread` 的那一面递给转录的说话人（用户 11-03：「消息要加在气泡上面旁边或者侧边
 * 加上 Agent 的名字和头像」）。名字与头像都取自名册那一行——与左栏同一份身份，不在这里
 * 再造一套。`console/chat/chat-surface.tsx` 是唯一给得上来的那一面；不给就没有抬头。
 */
export interface ThreadSpeaker {
  color?: string
  icon?: string
  name: string
}
