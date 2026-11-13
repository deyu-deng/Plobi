import { agentScope, chatScopeKey, L1_SCOPE } from '@/app/console/chat/scope'
import { shellLevelForPath } from '@/app/routes'

/**
 * 这一场对话的**队列桶** = 它那个 Agent 的作用域键。
 *
 * 规则只有一条：**桶＝这个 Agent，键永远存在**。
 *
 * 这个产品已经**没有会话概念**了——一 Agent 一张嘴（裁定 37／R-015，
 * `north-star-operating-model.md:89`），对话的身份就是 Agent 的 id。之前队列按
 * 会话 id 分桶，于是：① 一场新对话在拿到会话 id 之前**键是空的** ⇒ 入队静默失败
 * （用户 11-13：「输入后。发送不了了」），排队面板连同每条旁边的「立即发送」图标
 * 一起不渲染；② 键会随会话变（新会话／恢复／后端弹一次 id）⇒ 要么搬家，要么把
 * 同一条消息发两次。
 *
 * 按 Agent 分桶之后这两件事一起没了：桶**一辈子不变**（L1 一档、每个 L2 一档），
 * 会话换了桶也不换 ⇒ 不需要占位桶、不需要搬家。
 *
 * 键的形状**逐字复用控制台那套 `chatScopeKey`**（`app/console/chat/scope.ts`），
 * 所以「这一场对话」与「它的队列」用的是同一个身份——两处各写一份形状，迟早漂。
 */
export function agentScopeKey(pathname: string): string {
  const level = shellLevelForPath(pathname)

  if (!level) {
    // 裸会话路由（`/new`、`/sessions/:id` 那条已退役的路）：不是任何 Agent 的一张嘴。
    return 'chat'
  }

  return chatScopeKey(level === 'l1' ? L1_SCOPE : agentScope(level.l2))
}
