import { $composerAttachments, unquoteRefValue } from '@/store/composer'
import { quoteTextFor } from '@/store/quote'

/**
 * chip 的**标签**从哪儿来。
 *
 * 正文里的 chip 是**按线上形态重建**的（重绘一次就重建一遍），而线上形态里只有
 * kind 与 value（`@file:<路径>`、`@quote:<消息 id>`）——「屏上显示的那几个字」不在
 * 里面。所以重绘之后得从**真源**里取回来：
 *
 * - `quote` ⇒ 被引的那段原文（`store/quote.ts` 那张表）；
 * - 别的 kind ⇒ 那份附件的名字（`store/composer.ts` 那张表）。
 *
 * 取不到就退到 `refLabel(value)`（路径末段／id 本身）——宁可显示得糙一点，
 * 也不许那枚 chip 空着。
 */

/** 附件那一行：线上形态的值（`@file:`path`` ⇒ `path`）对得上吗？ */
function attachmentMatches(attachment: { path?: string; refText?: string }, value: string): boolean {
  if (attachment.path && attachment.path === value) {
    return true
  }

  if (!attachment.refText) {
    return false
  }

  const [, , rawValue] = /^@([^:]+):(.+)$/.exec(attachment.refText) ?? []

  return Boolean(rawValue && unquoteRefValue(rawValue) === value)
}

export function chipLabelFor(kind: string, value: string): string | undefined {
  if (kind === 'quote') {
    return quoteTextFor(value)
  }

  const attachment = $composerAttachments.get().find(item => attachmentMatches(item, value))

  return attachment?.label
}
