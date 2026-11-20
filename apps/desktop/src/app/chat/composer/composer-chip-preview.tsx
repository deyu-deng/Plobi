import { useStore } from '@nanostores/react'
import { type MouseEvent as ReactMouseEvent, useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { useI18n } from '@/i18n'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { cn } from '@/lib/utils'
import { $composerTerminalSelections, type ComposerAttachment } from '@/store/composer'
import { notifyError } from '@/store/notifications'
import { setCurrentSessionPreviewTarget } from '@/store/preview'
import { quoteTextFor } from '@/store/quote'
import { $currentCwd } from '@/store/session'

import { chipAttachmentFor } from './chip-labels'

/**
 * chip 的**悬浮预览卡**（用户 11-19：「用鼠标悬浮可以查看引用附件的预览」）。
 *
 * 形状就是用户 11-19 说的那个：**鼠标停在 chip 上就出一张小卡**——引用出那段原文，
 * 图出那张图，文件出它的名字与路径。
 *
 * 两条纪律：
 *
 * - **卡里没有新句子**。卡上摆的全是**数据**（被引的原文、文件名、路径、终端选区）
 *   ⇒ 四语一个字不用动（`i18n/` 是串行件，本刀不碰）。
 * - **卡是被看的，不是被点的**。但鼠标从 chip 挪到卡上时不能把卡弄没了 ⇒ 卡自己
 *   接管 mouseenter/mouseleave（`onDismiss`）。
 */

/** 卡里摆什么——全是数据，没有一句是新写的文案。 */
export interface ChipPreview {
  kind: string
  value: string
  /** 顶上那行（文件名／标签）。 */
  title?: string
  /** 正文（引用的那段原文、文件路径、终端选区）。 */
  body?: string
  /** 图（贴进来的图片）。 */
  imageUrl?: string
}

/** 那枚 chip（正文里 `data-ref-kind` / `data-ref-id` 那一个）——从事件目标往上找。 */
export function chipElementFrom(target: EventTarget | null): HTMLElement | null {
  // `Element` 而不是 `HTMLElement`：chip 里那个图标是 SVG，鼠标停在图标上也算停在 chip 上。
  if (!target || !(target instanceof Element)) {
    return null
  }

  const chip = target.closest('[data-ref-kind][data-ref-id]')

  return chip instanceof HTMLElement ? chip : null
}

/**
 * 那枚 chip 该摆出来的东西。
 *
 * 取不到就是取不到（`null`）——调用方据此不出卡，而不是摆一张空卡。
 */
export function chipPreviewFor(kind: string, value: string): ChipPreview | null {
  if (!kind || !value) {
    return null
  }

  if (kind === 'quote') {
    // 引用 ⇒ 被引的那段原文（**全文**，不截断——这正是 chip 标签给不出的那部分）。
    const text = quoteTextFor(value)

    return text ? { kind, value, body: text } : null
  }

  const attachment = chipAttachmentFor(kind, value)

  if (attachment) {
    const detail = attachment.detail && attachment.detail !== attachment.label ? attachment.detail : undefined

    return {
      kind,
      value,
      title: attachment.label,
      body: detail,
      imageUrl: attachment.kind === 'image' ? attachment.previewUrl : undefined
    }
  }

  if (kind === 'terminal') {
    // 终端选区不在附件表里（在 `$composerTerminalSelections`）。
    const text = $composerTerminalSelections.get()[value]

    return text ? { kind, value, title: value, body: text } : null
  }

  // 没有附件记录的那些 chip（重绘之后标签都退化了的）⇒ 至少把值本身摆出来。
  return { kind, value, title: value }
}

const CARD_MAX_WIDTH = 352
/** 上面放不下这么多高就改放下面。 */
const CARD_MIN_ABOVE = 160

/**
 * 卡片摆哪儿（相对视口）。
 *
 * 贴着 chip：上面放得下就放上面（`bottom`），放不下就放下面（`top`）；左边不许越界。
 */
export function chipPreviewPosition(
  anchor: { top: number; bottom: number; left: number },
  viewport: { width: number; height: number }
): { left: number; top?: number; bottom?: number } {
  const left = Math.min(Math.max(anchor.left, 8), Math.max(8, viewport.width - CARD_MAX_WIDTH - 8))

  if (anchor.top - 8 >= CARD_MIN_ABOVE) {
    return { left, bottom: Math.max(8, viewport.height - anchor.top + 8) }
  }

  return { left, top: Math.max(8, anchor.bottom + 8) }
}

/**
 * 点预览卡 ⇒ 打开右侧那个预览栏（文件／图的大图）。
 *
 * 附件那一行卡片退役之后（附件也是正文里的 chip），「看大图」这个动作挪到预览卡
 * 上——不然这条能力就没了。
 */
async function openPreviewPane(attachment: ComposerAttachment, cwd: string | undefined, copy: PreviewCopy) {
  const rawTarget =
    attachment.path ||
    attachment.detail ||
    attachment.refText?.replace(/^@(file|image|url):/, '') ||
    attachment.label ||
    ''

  const target = rawTarget.replace(/^`|`$/g, '')

  if (!target) {
    return
  }

  try {
    const preview = await normalizeOrLocalPreviewTarget(target, cwd || undefined)

    if (!preview) {
      throw new Error(copy.couldNotPreview(attachment.label))
    }

    // 手上已经有那份字节（缩略图就是它）⇒ 直接用它渲染（截图／剪贴板那张图，
    // 盘上的那个临时路径未必还读得到）。
    const withBytes =
      attachment.kind === 'image' && attachment.previewUrl
        ? { ...preview, dataUrl: attachment.previewUrl, previewKind: 'image' as const }
        : preview

    setCurrentSessionPreviewTarget(withBytes, 'manual', target)
  } catch (error) {
    notifyError(error, copy.previewUnavailable)
  }
}

interface PreviewCopy {
  couldNotPreview: (label: string) => string
  previewUnavailable: string
}

export function ChipPreviewCard({
  chip,
  onDismiss,
  preview
}: {
  chip: HTMLElement
  onDismiss: () => void
  preview: ChipPreview
}) {
  const { t } = useI18n()
  const cwd = useStore($currentCwd)
  const [viewport, setViewport] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }))

  useEffect(() => {
    const update = () => setViewport({ width: window.innerWidth, height: window.innerHeight })

    window.addEventListener('resize', update)
    // 捕捉阶段：编辑器内部的滚动也得算（chip 滚走了，卡得跟着走）。
    window.addEventListener('scroll', update, true)

    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [])

  // chip 没了（用户把它删了）⇒ 卡跟着没。
  const anchor = chip.isConnected ? chip.getBoundingClientRect() : null

  if (!anchor) {
    return null
  }

  const position = chipPreviewPosition(anchor, viewport)
  const attachment = chipAttachmentFor(preview.kind, preview.value)
  const canOpen = Boolean(attachment) && attachment!.kind !== 'folder' && attachment!.kind !== 'terminal'

  return createPortal(
    <div
      className={cn(
        'fixed z-[200] w-[22rem] max-w-[calc(100vw-1rem)] overflow-hidden rounded-xl border border-border/70 bg-background/95 shadow-xl',
        canOpen ? 'pointer-events-auto cursor-pointer' : 'pointer-events-none'
      )}
      data-slot="composer-chip-preview"
      onClick={
        canOpen
          ? () => {
              void openPreviewPane(attachment!, cwd || undefined, {
                couldNotPreview: t.composer.couldNotPreview,
                previewUnavailable: t.composer.previewUnavailable
              })
            }
          : undefined
      }
      onMouseLeave={onDismiss}
      style={position}
    >
      {preview.imageUrl && (
        <img
          alt=""
          className="max-h-56 w-full bg-muted/30 object-contain"
          draggable={false}
          src={preview.imageUrl}
        />
      )}
      {preview.title && (
        <div className="truncate px-3 pt-2 text-[0.72rem] font-medium leading-4 text-foreground/90">
          {preview.title}
        </div>
      )}
      {preview.body && (
        <div className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words px-3 py-2 text-[0.72rem] leading-5 text-muted-foreground">
          {preview.body}
        </div>
      )}
    </div>,
    document.body
  )
}

/**
 * 输入面上的**那套手势**（挂在编辑器的 `onMouseOver` / `onMouseOut` 上）。
 *
 * 抽成钩子是为了能**单独验**：接线（鼠标停在那枚 chip 上 ⇒ 出卡；移开 ⇒ 收卡）
 * 也得有判据，不能只靠眼看。
 */
export function useChipPreview() {
  const [state, setState] = useState<{ chip: HTMLElement; preview: ChipPreview } | null>(null)
  // 同一枚 chip 上乱动鼠标不要反复开卡（每次 mouseover 都在 chip 的字上触发）。
  const keyRef = useRef('')

  const close = useCallback(() => {
    keyRef.current = ''
    setState(null)
  }, [])

  const onMouseOver = useCallback(
    (event: ReactMouseEvent<HTMLElement>) => {
      const chip = chipElementFrom(event.target)
      const kind = chip?.dataset.refKind || ''
      const value = chip?.dataset.refId || ''

      if (!chip || !kind || !value) {
        close()

        return
      }

      const key = `${kind}:${value}`

      if (keyRef.current === key) {
        return
      }

      const preview = chipPreviewFor(kind, value)

      keyRef.current = preview ? key : ''
      setState(preview ? { chip, preview } : null)
    },
    [close]
  )

  const onMouseOut = useCallback(
    (event: ReactMouseEvent<HTMLElement>) => {
      const related = event.relatedTarget as HTMLElement | null

      // 挪到预览卡上（要接着看）或挪到另一枚 chip 上（马上会开新卡）⇒ 别急着关。
      if (related?.closest?.('[data-slot="composer-chip-preview"], [data-ref-kind][data-ref-id]')) {
        return
      }

      close()
    },
    [close]
  )

  return {
    card: state ? <ChipPreviewCard chip={state.chip} onDismiss={close} preview={state.preview} /> : null,
    onMouseOut,
    onMouseOver
  }
}
