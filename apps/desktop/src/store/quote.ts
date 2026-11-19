import { atom } from 'nanostores'

/**
 * 被引的那些条（裁定 196 ④「引用条」；11-19 起引用可以有多条、且长成正文里的 chip）。
 *
 * **这张表不是「待发清单」**——待发清单是**正文里那几枚 chip**（线上形态
 * `@quote:<消息 id>`，见 `app/chat/composer/composer-chips.ts`）。这张表只管一件事：
 * **这枚 chip 该显示什么**（被引的那段原文）。
 *
 * 为什么需要这张表：正文里的 chip 是**按线上形态重建**的（重绘一次就重建一遍），
 * 而线上形态里只有一个 id ⇒ 标签得从这张表取回来。否则用户引了一段话、正文重绘
 * 一次，那枚 chip 就只剩下一个光秃秃的消息 id。
 *
 * 换会话**不清这张表**：清它只会让草稿里那枚 chip 的标签退化（草稿是按会话存的，
 * 引用 chip 本来就跟着草稿走，不会串场）。这张表只是标签缓存。
 */
export type QuoteTarget = { id: string; text: string }

export const $quoteTargets = atom<QuoteTarget[]>([])

/** 记下一条被引的原文（同一条 id 覆盖）。 */
export function setQuoteTarget(target: QuoteTarget): void {
  const current = $quoteTargets.get()
  const index = current.findIndex(quote => quote.id === target.id)

  const next =
    index >= 0 ? current.map((quote, i) => (i === index ? target : quote)) : [...current, target]

  $quoteTargets.set(next)
}

/** 忘掉那几条（发出去之后清：标签缓存不该一直长着）。 */
export function removeQuoteTargets(ids: readonly string[]): void {
  if (!ids.length) {
    return
  }

  const drop = new Set(ids)
  const current = $quoteTargets.get()
  const next = current.filter(quote => !drop.has(quote.id))

  if (next.length !== current.length) {
    $quoteTargets.set(next)
  }
}

/** 某枚引用 chip 该显示的那段原文（取不到就是取不到——调用方自己兜底）。 */
export function quoteTextFor(id: string): string | undefined {
  return $quoteTargets.get().find(quote => quote.id === id)?.text
}
