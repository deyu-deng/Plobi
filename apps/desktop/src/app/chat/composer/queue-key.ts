import { shellLevelForPath } from '@/app/routes'
import { pendingQueueKey } from '@/store/composer-queue'

/**
 * The queue bucket a conversation's pending messages live in.
 *
 * The rule this names: **the key always exists**. A conversation that has no
 * session id yet — a brand-new chat whose first message is still on its way to
 * the gateway — used to key the queue with `null`, and the queue store dropped
 * the message on the floor (user 11-13: 「输入后。发送不了了」).
 *
 * So the last resort is a placeholder bucket, one per Agent (see
 * `pendingQueueScope`): two Agents' new chats never share one, or a pending
 * message would surface in the wrong conversation. The placeholder bucket is
 * carried onto the real session when that conversation gets its session id —
 * that move happens where the session is minted, because only there is it
 * known which conversation the placeholder belongs to.
 */

/**
 * 「还没有会话」的那一场的队列作用域（按 Agent 分桶）。
 *
 * 身份改成以 Agent 为单位之后，会话 id 不再**先于**对话存在：一场新对话在第
 * 一条消息落地之前既没有存着的会话 id，也没有运行时 id ⇒ 队列键为空 ⇒ 排队
 * 静默失败（用户：输入后发送不了了）。所以队列退到一个占位键上，而占位键按
 * **Agent** 分桶——两个 Agent 的新对话不许共用一个桶，否则一条排队的消息会跑
 * 到别的 Agent 那场去。
 *
 * L1（`/`、`/console`）是一档，每个 L2 秘书（`/agent/:id`）一档，其余（裸会话
 * 路由）一档。
 */
export function pendingQueueScope(pathname: string): string {
  const level = shellLevelForPath(pathname)

  if (level === 'l1') {
    return 'l1'
  }

  return level ? level.l2 : 'chat'
}

/** The queue bucket for a conversation — never empty. */
export const composerQueueKey = (params: {
  pathname: string
  queueSessionKey?: null | string
  sessionId?: null | string
}): string => params.queueSessionKey || params.sessionId || pendingQueueKey(pendingQueueScope(params.pathname))
