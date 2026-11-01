/**
 * 右栏里的「一条日程」（用户 10-31）。
 *
 * 点轴上的块 → 这一栏**就地**换成这条日程的详情，改完点返回回到时间轴。
 * 渲染和动作都复用看板那一套（`app/agenda/detail` + `app/agenda/actions`），
 * 这里只补三件右栏特有的事：
 *   - 一个返回条（不然进去出不来）；
 *   - 附件点了在当前右栏新开页签看内容，而不是甩给系统浏览器；
 *   - 编辑弹窗自己持有一份编辑态（看板那份在它自己的组件里）。
 */

import { useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import type { AgendaAttachment, AgendaEvent } from '@/types/plobi'

import { saveAgendaEditorValues, useAgendaEventActions } from '../../agenda/actions'
import {
  AgendaDetail,
  AgendaEditorDialog,
  type AgendaEditorValues,
  type EditorState
} from '../../agenda/detail'

export function RailEventDetail({
  event,
  onBack,
  onOpenAttachment
}: {
  event: AgendaEvent
  onBack: () => void
  /** 点附件：调用方在当前右栏新开页签（L1 走预览页签，L2 走文件页签）。 */
  onOpenAttachment?: (attachment: AgendaAttachment) => void
}) {
  const { t } = useI18n()
  const a = t.agenda
  const actions = useAgendaEventActions()
  const [editor, setEditor] = useState<EditorState>({ mode: 'closed' })

  async function handleSave(values: AgendaEditorValues) {
    await saveAgendaEditorValues(values, editor.mode === 'edit' ? editor.event : undefined)
    setEditor({ mode: 'closed' })
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-(--ui-stroke-tertiary) px-1">
        <button
          aria-label={t.common.back}
          className="grid size-6 shrink-0 place-items-center rounded-md text-(--ui-text-secondary) hover:bg-(--ui-surface-2) hover:text-foreground"
          data-testid="rail-event-back"
          onClick={onBack}
          title={t.common.back}
          type="button"
        >
          <Codicon name="chevron-left" size="0.85rem" />
        </button>
        <span className="min-w-0 truncate text-[0.72rem] text-(--ui-text-secondary)">
          {t.workbench.tabs.agenda}
        </span>
      </div>

      {/* PanelDetail 自带 flex-1 + overflow-y-auto，这里只给一层 flex 容器，
          不要再套第二个滚动区（会套出双滚动条）。 */}
      <div className="flex min-h-0 flex-1 flex-col" data-testid="rail-event-detail">
        <AgendaDetail
          a={a}
          attachmentsBusy={actions.attachmentBusy === event.id}
          busy={actions.busyId === event.id}
          event={event}
          onAddAttachment={() => void actions.addAttachments(event)}
          onConfirm={() => void actions.confirm(event)}
          onDelete={() => void actions.remove(event)}
          onDismiss={() => void actions.dismiss(event)}
          onEdit={() => setEditor({ event, mode: 'edit' })}
          onOpenAttachment={onOpenAttachment}
          onRemoveAttachment={attachment => void actions.removeAttachment(event, attachment)}
          onToggleDetail={index => void actions.toggleDetail(event, index)}
        />
      </div>

      <AgendaEditorDialog
        a={a}
        editor={editor}
        onClose={() => setEditor({ mode: 'closed' })}
        onSave={handleSave}
      />
    </div>
  )
}
