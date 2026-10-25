import { atom } from 'nanostores'

/**
 * 待引用的一条消息（裁定 196 ④「引用条」）。
 *
 * 只存 id 与一段预览：点气泡上的「引用」时写进来，输入框上方那条引用条读它，
 * 发送时作为 `reply_to` 带出去，成功后清掉。切会话/关掉那条时也要清——
 * 引用只对**同一屏**里的一条消息有意义。
 */
export type QuoteTarget = { id: string; text: string }

export const $quoteTarget = atom<null | QuoteTarget>(null)

export function setQuoteTarget(target: QuoteTarget): void {
  $quoteTarget.set(target)
}

export function clearQuoteTarget(): void {
  $quoteTarget.set(null)
}
