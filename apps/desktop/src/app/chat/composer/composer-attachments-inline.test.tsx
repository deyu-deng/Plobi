// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'

import { $composerAttachments, type ComposerAttachment, withChipValue } from '@/store/composer'

import {
  attachmentForChip,
  attachmentIntoComposer,
  attachmentsForChips,
  messageBodyFromText,
  stripAttachmentChips
} from './composer-chips'
import { onComposerInsertRefsRequest } from './focus'
import type { InlineRefInput } from './inline-refs'
import { renderComposerContents, syncChipUploadStates } from './rich-editor'

/**
 * 附件也变**正文里的一枚 chip**（用户 11-19 看 WorkBuddy 之后拍板）。
 *
 * 11-19 之前附件是输入面**顶部**那一行卡片：跟正文分家、顺序另排一份、删要专门
 * 点那个 ×。现在附件就是正文里的一枚 chip：跟文字同一行、按加入顺序排、跟引用
 * chip 一样删得掉、悬浮出预览。
 *
 * 由此带来的一条硬规矩：**待发清单就是正文**——发出去的附件**从正文里的 chip
 * 反推**（用户删掉一枚 chip，那份附件自然不发），而附件 chip 的线上形态**不进
 * 正文**（它们的上下文走附件那条道；进正文就是同一份文件被引两次）。
 *
 * 这一档钉的是：
 * ① 附件进正文 ⇒ 插一枚 chip（kind／value／label 都对）；
 * ② 发出去的附件**从正文里的 chip 反推**，按正文顺序，删掉就不发；
 * ③ 附件 chip 从正文里剥掉；**用户自己敲的 `@file:` 留着**（后端认得它）；
 * ④ 上传成功后 `path` 被改写，正文里那枚 chip 仍认得出那份附件；
 * ⑤ 上传失败 ⇒ chip 身上有状态标记（状态是异步的 ⇒ 回头标一次）。
 */

afterEach(() => {
  $composerAttachments.set([])
})

const fileAttachment = (id: string, value: string, label: string) =>
  withChipValue({ id, kind: 'file', label, refText: `@file:\`${value}\`` })

describe('附件进正文', () => {
  it('插一枚 chip（kind=file、value=那个路径、label=它的名字）', async () => {
    const seen: InlineRefInput[][] = []
    const off = onComposerInsertRefsRequest(({ refs }) => seen.push(refs))

    attachmentIntoComposer(fileAttachment('file:a', 'docs/a.md', 'a.md'))
    // 那个事件是 `setTimeout(0)` 派发的 ⇒ 等它落地再收摊。
    await new Promise(resolve => setTimeout(resolve, 0))
    off()

    expect(seen[0]?.[0]).toMatchObject({ kind: 'file', value: 'docs/a.md', label: 'a.md' })
  })

  it('重绘之后 chip 的标签还在（从附件那张表取回）', () => {
    $composerAttachments.set([fileAttachment('file:a', 'docs/a.md', 'a.md')])

    const editor = document.createElement('div')

    document.body.append(editor)
    renderComposerContents(editor, '@file:`docs/a.md`')

    expect(editor.querySelector('[data-ref-kind="file"]')?.textContent).toContain('a.md')
  })
})

describe('发出去的附件从正文里的 chip 反推', () => {
  it('正文里有哪几枚 chip，就发哪几份附件', () => {
    $composerAttachments.set([fileAttachment('file:a', 'docs/a.md', 'a.md'), fileAttachment('file:b', 'docs/b.md', 'b.md')])

    expect(attachmentsForChips('@file:`docs/a.md` 你看').map(item => item.id)).toEqual(['file:a'])
  })

  it('顺序跟正文一致', () => {
    $composerAttachments.set([fileAttachment('file:a', 'docs/a.md', 'a.md'), fileAttachment('file:b', 'docs/b.md', 'b.md')])

    expect(attachmentsForChips('@file:`docs/b.md` @file:`docs/a.md`').map(item => item.id)).toEqual([
      'file:b',
      'file:a'
    ])
  })

  it('删掉 chip ⇒ 那份附件不发（待发清单就是正文）', () => {
    $composerAttachments.set([fileAttachment('file:a', 'docs/a.md', 'a.md')])

    expect(attachmentsForChips('你看看这个')).toEqual([])
  })

  it('同一份附件只算一次', () => {
    $composerAttachments.set([fileAttachment('file:a', 'docs/a.md', 'a.md')])

    expect(attachmentsForChips('@file:`docs/a.md` @file:`docs/a.md`').map(item => item.id)).toEqual(['file:a'])
  })

  it('附件表里有空洞（undefined）也不崩（切会话会留下那种行——#49624）', () => {
    $composerAttachments.set([
      undefined as unknown as ComposerAttachment,
      fileAttachment('file:a', 'docs/a.md', 'a.md')
    ])

    expect(attachmentsForChips('@file:`docs/a.md`').map(item => item.id)).toEqual(['file:a'])
  })
})

describe('正文里剥掉附件 chip', () => {
  it('附件 chip 不进正文（上下文走附件那条道）', () => {
    $composerAttachments.set([fileAttachment('file:a', 'docs/a.md', 'a.md')])

    expect(messageBodyFromText('@file:`docs/a.md` 你看')).toBe('你看')
  })

  it('用户自己敲的 `@file:` 留着（后端认得它，会去解析成上下文）', () => {
    expect(messageBodyFromText('@file:`docs/a.md` 你看')).toBe('@file:`docs/a.md` 你看')
  })

  it('图 chip 一律剥（图只走附件那条道，后端不认识这个 kind）', () => {
    expect(stripAttachmentChips('@image:`D:\\tmp\\shot.png` 你看')).toBe('你看')
  })
})

describe('上传把路径改了之后', () => {
  it('正文里那枚 chip 仍认得出那份附件（认的是 chipValue，不是 path）', () => {
    $composerAttachments.set([
      {
        id: 'image:1',
        kind: 'image',
        label: 'shot.png',
        path: 'D:\\gateway\\shot.png',
        chipValue: 'D:\\tmp\\shot.png'
      }
    ])

    expect(attachmentForChip({ kind: 'image', value: 'D:\\tmp\\shot.png' })?.id).toBe('image:1')
  })
})

describe('上传状态贴在 chip 上', () => {
  it('上传失败 ⇒ chip 身上有状态标记', () => {
    $composerAttachments.set([
      { ...fileAttachment('file:a', 'docs/a.md', 'a.md'), uploadState: 'error' }
    ])

    const editor = document.createElement('div')

    document.body.append(editor)
    renderComposerContents(editor, '@file:`docs/a.md`')

    expect(editor.querySelector('[data-ref-kind="file"]')?.getAttribute('data-upload-state')).toBe('error')
  })

  it('状态是异步的 ⇒ 回头标到已经建好的 chip 上', () => {
    const editor = document.createElement('div')

    document.body.append(editor)
    renderComposerContents(editor, '@file:`docs/a.md`')

    $composerAttachments.set([{ ...fileAttachment('file:a', 'docs/a.md', 'a.md'), uploadState: 'error' }])
    syncChipUploadStates(editor)

    expect(editor.querySelector('[data-ref-kind="file"]')?.getAttribute('data-upload-state')).toBe('error')
  })
})
