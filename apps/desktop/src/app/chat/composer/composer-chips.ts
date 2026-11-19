import { quoteTextFor, setQuoteTarget } from '@/store/quote'

import { requestComposerInsertRefs } from './focus'
import { REF_RE, unquoteRef } from './rich-editor'

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
 * - **`@quote:` 是元数据，不是正文**。发出去之前要剥掉（agent 侧不认识这个 kind，
 *   留着就是一句机器词）。别的 chip **不剥**——后端认得 `@file:` 那些，会去解析
 *   成上下文。
 */

/** 引用 chip 的 kind。 */
export const QUOTE_CHIP_KIND = 'quote'

export interface ComposerChip {
  kind: string
  value: string
}

/** chip 的线上形态（`@quote:`12`` 这种带引号的值也要认全）。 */
const QUOTE_TOKEN_RE = /@quote:(?:`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|\S+)/g

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
 * 剥掉引用 chip 的线上形态（发出去之前）。
 *
 * chip 自带的那一个空格（退格删 chip 时也会带走它）跟着一块儿剥：后面有就剥后面
 * 那个，后面没有就剥前面那个——剥完不留两格空格。
 */
export function stripQuoteChips(text: string): string {
  if (!text.includes('@quote:')) {
    return text
  }

  let out = ''
  let index = 0

  for (const match of text.matchAll(QUOTE_TOKEN_RE)) {
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

/**
 * 这一条要发出去的正文。
 *
 * 剥掉引用 chip 之后如果**一个字都不剩**（用户只引用、没打字），退回被引的那段
 * 原文——发一条空消息给 agent 没有任何意义。
 */
export function messageBodyFromText(text: string): string {
  const body = stripQuoteChips(text)

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
