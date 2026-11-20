import { attachmentForChipValue, type ComposerAttachment } from '@/store/composer'
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

export function chipLabelFor(kind: string, value: string): string | undefined {
  if (kind === 'quote') {
    return quoteTextFor(value)
  }

  return chipAttachmentFor(kind, value)?.label
}

/**
 * 那枚 chip 对得上的附件（名字、路径、缩略图都从这儿取）。
 *
 * 认附件的规矩在 `store/composer.ts::attachmentForChipValue`（标签、预览卡、发送
 * 三边都从那儿认，别处别再写第二份）。
 */
export function chipAttachmentFor(kind: string, value: string): ComposerAttachment | undefined {
  if (kind === 'quote') {
    return undefined
  }

  return attachmentForChipValue(kind, value)
}
