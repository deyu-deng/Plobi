/**
 * 一条日程的所有写动作（用户 10-31 从 `app/agenda/index.tsx` 抽出来）。
 *
 * 抽出来的理由只有一个：右栏点块之后要能**改**这条日程——勾清单、加附件、确认
 * 待办、编辑、删除。这些动作原来只住在看板里，右栏要么再写一套（两份真源，必然
 * 漂移），要么只能只读。现在两边共用这一个 hook。
 *
 * 这里只管动作和忙碌态，不管渲染（渲染在 `./detail`）。
 */

import { useState } from 'react'

import { useI18n } from '@/i18n'
import { selectDesktopPaths } from '@/lib/desktop-fs'
import {
  addAgendaAttachment,
  confirmAgendaEvent,
  createAgendaEvent,
  deleteAgendaEvent,
  dismissAgendaEvent,
  removeAgendaAttachment,
  updateAgendaEvent
} from '@/plobi'
import { removeAgendaEvent, setAgendaSelectedId, upsertAgendaEvent } from '@/store/agenda'
import { notify, notifyError } from '@/store/notifications'
import type { AgendaAttachment, AgendaEvent } from '@/types/plobi'

import type { AgendaEditorValues } from './detail'

function isDeleted(value: unknown): value is { deleted: true; id: string } {
  return typeof value === 'object' && value !== null && 'deleted' in value
}

/**
 * 编辑器保存：给了 `target` 就是改，没给就是新建。
 *
 * 编辑器故意让 `null` 与 `undefined` 有区别：字段缺省＝后端保留原值，显式 null
 * ＝把这个字段清空。这条口径原样保留。
 */
export async function saveAgendaEditorValues(
  values: AgendaEditorValues,
  target?: AgendaEvent
): Promise<void> {
  if (target) {
    const patch = {
      details: values.details,
      end_at: values.endAt,
      kind: values.kind,
      location: values.location,
      notes: values.notes,
      start_at: values.startAt,
      title: values.title
    }

    upsertAgendaEvent(await updateAgendaEvent(target.id, patch))

    return
  }

  const created = await createAgendaEvent({
    details: values.details,
    end_at: values.endAt ?? undefined,
    kind: values.kind,
    location: values.location,
    notes: values.notes,
    start_at: values.startAt,
    title: values.title
  })

  upsertAgendaEvent(created)
  setAgendaSelectedId(created.id)
}

export interface AgendaEventActions {
  addAttachments: (event: AgendaEvent) => Promise<void>
  attachmentBusy: null | string
  busyId: null | string
  confirm: (event: AgendaEvent) => Promise<void>
  dismiss: (event: AgendaEvent) => Promise<void>
  remove: (event: AgendaEvent) => Promise<void>
  removeAttachment: (event: AgendaEvent, attachment: AgendaAttachment) => Promise<void>
  toggleDetail: (event: AgendaEvent, index: number) => Promise<void>
}

export function useAgendaEventActions(): AgendaEventActions {
  const { t } = useI18n()
  const a = t.agenda
  const [busyId, setBusyId] = useState<null | string>(null)
  const [attachmentBusy, setAttachmentBusy] = useState<null | string>(null)

  async function confirm(event: AgendaEvent) {
    setBusyId(event.id)

    try {
      upsertAgendaEvent(await confirmAgendaEvent(event.id))
      notify({ message: a.confirmed })
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  async function dismiss(event: AgendaEvent) {
    setBusyId(event.id)

    try {
      const result = await dismissAgendaEvent(event.id)

      if (isDeleted(result)) {
        removeAgendaEvent(event.id)
      } else {
        upsertAgendaEvent(result)
      }

      notify({ message: a.dismissed })
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  async function remove(event: AgendaEvent) {
    setBusyId(event.id)

    try {
      await deleteAgendaEvent(event.id)
      removeAgendaEvent(event.id)
      notify({ message: a.deleted })
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  async function addAttachments(event: AgendaEvent) {
    try {
      const paths = await selectDesktopPaths({ multiple: true })

      if (paths.length === 0) {
        return
      }

      setAttachmentBusy(event.id)
      let latest = event

      for (const path of paths) {
        latest = await addAgendaAttachment(event.id, path)
      }

      upsertAgendaEvent(latest)
      notify({ message: a.attachmentAdded })
    } catch (cause) {
      notifyError(cause, a.attachmentFailed)
    } finally {
      setAttachmentBusy(null)
    }
  }

  async function removeAttachment(event: AgendaEvent, attachment: AgendaAttachment) {
    setAttachmentBusy(event.id)

    try {
      upsertAgendaEvent(await removeAgendaAttachment(event.id, attachment.id))
      notify({ message: a.attachmentRemoved })
    } catch (cause) {
      notifyError(cause, a.attachmentFailed)
    } finally {
      setAttachmentBusy(null)
    }
  }

  async function toggleDetail(event: AgendaEvent, index: number) {
    const details = (event.details ?? []).map((row, position) =>
      position === index ? { ...row, done: !row.done } : row
    )

    try {
      upsertAgendaEvent(await updateAgendaEvent(event.id, { details }))
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    }
  }

  return { addAttachments, attachmentBusy, busyId, confirm, dismiss, remove, removeAttachment, toggleDetail }
}
