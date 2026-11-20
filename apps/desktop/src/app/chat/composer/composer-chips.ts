import { attachmentChipValue, attachmentForChipValue, type ComposerAttachment } from '@/store/composer'
import { quoteTextFor, setQuoteTarget } from '@/store/quote'

import { requestComposerInsertRefs } from './focus'
import { quoteRefValue, REF_RE, unquoteRef } from './rich-editor'

/**
 * 正文里那些 **chip** —— 引用、贴的图片、别的附件，都在正文里（跟文字同一行），
 * 按它们在正文里的位置排（那就是加入顺序）。
 *
 * chip 的**线上形态**是 `@kind:value`（`value` 带引号时整段包住）：
 * `@quote:<消息 id>`、`@image:<路径>`、`@file:<路径>`……
 *
 * 两件要紧事：
 *
 * - **待发清单就是正文**。用户删掉一枚 chip，那一份上下文就没了；用户把 chip 拖到
 *   别的位置，顺序就跟着变。正文是唯一真源，别处别再维护第二份清单。
 * - **chip 分两类**：**元数据**（`@quote:`——锚点单独带走，正文里不留）与
 *   **附件**（`@file:`／`@image:` 那些——上下文走附件那条道，正文里也不留）。
 *   用户自己敲的 `@file:` 不算附件，**留着**（后端认得它，会去解析成上下文）。
 */

/** 引用 chip 的 kind。 */
export const QUOTE_CHIP_KIND = 'quote'

export interface ComposerChip {
  kind: string
  value: string
}

/** 正文里那些 chip（按正文顺序）。 */
export function chipsInText(text: string): ComposerChip[] {
  const chips: ComposerChip[] = []

  for (const match of text.matchAll(REF_RE)) {
    chips.push({ kind: match[1] || 'file', value: unquoteRef(match[2] || '') })
  }

  return chips
}

/** 正文里那些引用的 id（发送时的 `reply_to`，按正文顺序）。 */
export function quoteIdsInText(text: string): string[] {
  return chipsInText(text)
    .filter(chip => chip.kind === QUOTE_CHIP_KIND)
    .map(chip => chip.value)
}

/**
 * 剥掉正文里那些 chip（`shouldStrip` 说了算）。
 *
 * chip 自带的那一个空格（退格删 chip 时也会带走它）跟着一块儿剥：后面有就剥后面
 * 那个，后面没有就剥前面那个——剥完不留两格空格。
 */
function stripChips(text: string, shouldStrip: (chip: ComposerChip) => boolean): string {
  if (!text.includes('@')) {
    return text
  }

  let out = ''
  let index = 0

  for (const match of text.matchAll(REF_RE)) {
    const chip = { kind: match[1] || 'file', value: unquoteRef(match[2] || '') }

    if (!shouldStrip(chip)) {
      continue
    }

    const start = match.index ?? 0
    const end = start + match[0].length

    out += text.slice(index, start)
    index = end

    if (text[end] === ' ') {
      index = end + 1
    } else if (out.endsWith(' ')) {
      out = out.slice(0, -1)
    }
  }

  return out + text.slice(index)
}

/** 引用 chip ⇒ 剥掉（它是元数据不是正文；锚点单独带走）。 */
export function stripQuoteChips(text: string): string {
  return stripChips(text, chip => chip.kind === QUOTE_CHIP_KIND)
}

/**
 * 附件 chip ⇒ 剥掉（它们的上下文走**附件那条道**：进正文就是同一份文件被引两次）。
 *
 * - `@image:` 一律剥：图只走附件那条道（上传／`image.attach`），后端不认识这个 kind。
 * - `@file:`／`@folder:`／`@url:` 只在**有附件跟着**的时候剥——用户自己敲的那些
 *   留着，后端认得它们（`agent/context_references.py` 的词表）。
 */
export function stripAttachmentChips(text: string): string {
  return stripChips(text, chip => {
    if (chip.kind === QUOTE_CHIP_KIND) {
      return false
    }

    if (chip.kind === 'image') {
      return true
    }

    return attachmentForChip(chip) !== undefined
  })
}

/**
 * 这一条要发出去的正文。
 *
 * 剥掉引用 chip 与附件 chip 之后如果**一个字都不剩**（用户只引用、没打字），退回
 * 被引的那段原文——发一条空消息给 agent 没有任何意义。
 */
export function messageBodyFromText(text: string): string {
  const body = stripAttachmentChips(stripQuoteChips(text))

  if (body.trim()) {
    return body
  }

  const first = quoteIdsInText(text)[0]

  return first ? (quoteTextFor(first) ?? '').trim() : body
}

/** 引用 chip 的标签：被引的那段原文（压成一行，截断）。 */
export function quoteChipLabel(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()

  return flat.length > 40 ? `${flat.slice(0, 40)}…` : flat
}

/**
 * 那枚 chip 对得上的附件（**正文 ↔ 附件**之间的钥匙）。
 *
 * 认附件的规矩在 `store/composer.ts::attachmentForChipValue`（标签、预览卡、发送
 * 三边都从那儿认，别处别再写第二份）。
 */
export function attachmentForChip(chip: ComposerChip): ComposerAttachment | undefined {
  if (chip.kind === QUOTE_CHIP_KIND) {
    return undefined
  }

  return attachmentForChipValue(chip.kind, chip.value)
}

/**
 * 正文里那几枚 chip 对应的附件（**按正文顺序**；同一份附件只算一次）。
 *
 * 这就是「发出去的附件从正文里的 chip 反推」——用户删掉一枚 chip，那份附件就没了。
 */
export function attachmentsForChips(text: string): ComposerAttachment[] {
  const out: ComposerAttachment[] = []
  const seen = new Set<string>()

  for (const chip of chipsInText(text)) {
    const attachment = attachmentForChip(chip)

    if (!attachment || seen.has(attachment.id)) {
      continue
    }

    seen.add(attachment.id)
    out.push(attachment)
  }

  return out
}

/**
 * 附件 ⇒ **在正文里插一枚 chip**（跟引用 chip 一样：跟文字同一行、按加入顺序排）。
 *
 * 11-19 之前附件是输入面**顶部**那一行卡片（跟正文分家、顺序另排一份）。现在附件
 * 就是正文里的一枚 chip：待发清单只有正文一份。
 */
export function attachmentIntoComposer(attachment: ComposerAttachment): void {
  const value = attachmentChipValue(attachment)

  if (!value) {
    return
  }

  requestComposerInsertRefs([{ kind: attachment.kind, value, label: attachment.label }], { target: 'main' })
}

/** 附件的线上形态（`@file:`path``）。 */
function attachmentChipText(attachment: ComposerAttachment): string {
  return `@${attachment.kind}:${quoteRefValue(attachmentChipValue(attachment))}`
}

/**
 * 把附件的 chip **补回正文**（正文里还没有的那些）。
 *
 * 用在哪：排队的那一条被拉回来编辑时——队列里存的是**剥掉 chip 的正文**（发出去
 * 不带机器词），可附件得重新变成正文里的 chip，否则「待发清单就是正文」这条规矩
 * 在编辑队列时就不成立（附件会悄悄丢掉）。
 */
export function textWithAttachmentChips(text: string, attachments: ComposerAttachment[]): string {
  if (!attachments.length) {
    return text
  }

  const present = chipsInText(text)
  const missing = attachments.filter(item => !present.some(chip => attachmentForChip(chip)?.id === item.id))

  if (!missing.length) {
    return text
  }

  const chips = missing.map(attachmentChipText).join(' ')

  return text.trim() ? `${text.trimEnd()} ${chips}` : chips
}

/**
 * 引用一条消息 ⇒ **在正文里插一枚 chip**（跟文字同一行）。
 *
 * 11-19 之前引用是输入框**上方**的一条灰条（用户 11-19 报障：那条删不掉——
 * 它被输入面底下那层「拖动区」盖住了）。现在引用就是正文里的一枚 chip：
 * 跟文字同一行、跟别的附件一起按加入顺序排、删得掉。
 *
 * 引用的**原文**记在 `store/quote.ts` 那张表里（chip 重绘时标签从那儿取回）。
 */
export function quoteIntoComposer(id: string, text: string): void {
  if (!id || !text.trim()) {
    return
  }

  setQuoteTarget({ id, text })
  requestComposerInsertRefs([{ kind: QUOTE_CHIP_KIND, value: id, label: quoteChipLabel(text) }], {
    target: 'main'
  })
}
