import { useEffect, useState } from 'react'

import { OffloadVendorAvatar } from '@/app/chat/sidebar/offload-vendor-icon'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import {
  deleteExternalSession,
  getAgentExternalSessions,
  getExternalTranscript,
  groupByVendor,
  patchExternalSession,
  restoreExternalSession,
  sendExternalMessage,
  titleOf,
  vendorLabel,
  whenText
} from './external-sessions'
import type { ExternalMessage, ExternalSession, ExternalSessionsData } from './external-sessions'

/**
 * 左栏「外包给」下方那一组：这台机器上各家 AI 软件里**跟这个 Agent 有关**的会话
 * （WP-EXTERNAL-SESSIONS）。应用图标 → 展开 → 点一条在 Plobi 里读、在这里说、改名/归档/删除。
 *
 * 只显示 `related === true` 的；`null` 是「L2 还没判断」，收在开关里；`false` 不出现。
 * 归档的折在底部；删除＝搬进本机回收站（可恢复）。改名只在我们这一侧生效——
 * 那家软件自己的界面不跟着变（它的索引是另一套，实测如此）。
 */
export function ExternalSessionsPanel({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const c = t.console
  const [data, setData] = useState<null | ExternalSessionsData>(null)
  const [open, setOpen] = useState<null | ExternalSession>(null)
  const [openVendor, setOpenVendor] = useState<null | string>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [showUnknown, setShowUnknown] = useState(false)
  const [notice, setNotice] = useState<null | { id: string, vendor: string }>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const reload = () => setReloadKey(value => value + 1)

  useEffect(() => {
    let cancelled = false

    const load = (first: boolean) => {
      if (first) {
        setData(null)
      }

      getAgentExternalSessions(agentId)
        .then(body => {
          if (!cancelled) {
            setData(body?.data ?? { projects: [], reason: '', sessions: [] })
          }
        })
        .catch(() => {
          // 读到过的表不清空：一次抖动不该把「它有哪些会话」抹成读取失败。
          if (!cancelled && first) {
            setData({ projects: [], reason: c.externalSessionsLoadFailed, sessions: [] })
          }
        })
    }

    load(true)
    // L2 在聊天里标完/改了名，这一格要自己收窄——不用再点一次。
    const timer = setInterval(() => load(false), 5000)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [agentId, c.externalSessionsLoadFailed, reloadKey])

  const all = data?.sessions ?? []
  const active = all.filter(session => !session.archived)
  const archived = all.filter(session => session.archived)
  const related = active.filter(session => session.related === true)
  const unknown = active.filter(session => session.related === null)
  // 一条相关的都没有时，先把「待判断」摆出来（按应用分组）——否则第一次打开只是一句空话。
  const visible = showUnknown || related.length === 0 ? [...related, ...unknown] : related
  const groups = groupByVendor(visible)

  const restore = () => {
    if (!notice) {
      return
    }

    restoreExternalSession(agentId, notice.vendor, notice.id)
      .then(() => {
        setNotice(null)
        reload()
      })
      .catch(() => undefined)
  }

  return (
    <div className="flex flex-col gap-px">
      <span className="px-1 text-[0.7rem] uppercase text-(--ui-text-tertiary)">{c.externalSessionsTitle}</span>
      {data !== null && (related.length > 0 || unknown.length > 0) && (
        <span className="px-2 text-[0.65rem] text-(--ui-text-quaternary)">
          {c.externalSessionsRelated} {related.length} · {c.externalSessionsShowUnknown} {unknown.length}
        </span>
      )}

      {data === null && (
        <div className="px-3 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">
          {c.externalSessionsLoading}
        </div>
      )}

      {data !== null && visible.length === 0 && archived.length === 0 && (
        <div className="px-3 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">
          {data.reason || c.externalSessionsEmpty}
        </div>
      )}

      {groups.map(group => {
        const expanded = openVendor === group.vendor

        return (
          <div key={group.vendor}>
            <button
              className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[0.78rem] hover:bg-(--ui-bg-secondary)"
              onClick={() => setOpenVendor(expanded ? null : group.vendor)}
              type="button"
            >
              <OffloadVendorAvatar vendor={group.vendor} />
              <span className="min-w-0 flex-1 truncate">{vendorLabel(group.vendor)}</span>
              <span className="shrink-0 text-[0.65rem] text-(--ui-text-quaternary)">{group.sessions.length}</span>
            </button>

            {expanded && (
              <div className="flex flex-col gap-px pl-5">
                {group.sessions.map(session => (
                  <button
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left hover:bg-(--ui-bg-secondary)"
                    key={`${session.vendor}-${session.id}`}
                    onClick={() => setOpen(session)}
                    title={session.project}
                    type="button"
                  >
                    <span className="min-w-0 flex-1 truncate text-[0.75rem]">{titleOf(session)}</span>
                    <span className="shrink-0 text-[0.62rem] text-(--ui-text-quaternary)">
                      {whenText(session.updated_at)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })}

      {unknown.length > 0 && (
        <button
          className="px-2 py-0.5 text-left text-[0.68rem] text-(--ui-text-quaternary) hover:text-(--ui-text-secondary)"
          onClick={() => setShowUnknown(value => !value)}
          type="button"
        >
          {showUnknown ? c.externalSessionsHideUnknown : `${c.externalSessionsShowUnknown} (${unknown.length})`}
        </button>
      )}

      {archived.length > 0 && (
        <div className="flex flex-col gap-px">
          <button
            className="px-2 py-0.5 text-left text-[0.68rem] text-(--ui-text-quaternary) hover:text-(--ui-text-secondary)"
            onClick={() => setShowArchived(value => !value)}
            type="button"
          >
            {c.externalSessionsArchived} ({archived.length})
          </button>
          {showArchived &&
            archived.map(session => (
              <button
                className="flex w-full items-center gap-2 rounded-md px-2 py-1 pl-5 text-left hover:bg-(--ui-bg-secondary)"
                key={`archived-${session.vendor}-${session.id}`}
                onClick={() => setOpen(session)}
                type="button"
              >
                <span className="min-w-0 flex-1 truncate text-[0.75rem] text-(--ui-text-tertiary)">
                  {titleOf(session)}
                </span>
                <span className="shrink-0 text-[0.62rem] text-(--ui-text-quaternary)">
                  {vendorLabel(session.vendor)}
                </span>
              </button>
            ))}
        </div>
      )}

      {notice && (
        <div className="flex items-center gap-2 px-2 py-0.5 text-[0.68rem] text-(--ui-text-tertiary)">
          <span className="min-w-0 flex-1 truncate">{c.externalSessionsDeleted}</span>
          <button className="shrink-0 text-(--ui-accent) hover:underline" onClick={restore} type="button">
            {c.externalSessionsRestore}
          </button>
        </div>
      )}

      {open && (
        <TranscriptSheet
          agentId={agentId}
          onChanged={reload}
          onClose={() => setOpen(null)}
          onDeleted={session => {
            setOpen(null)
            setNotice({ id: session.id, vendor: session.vendor })
            reload()
          }}
          session={open}
        />
      )}
    </div>
  )
}

function TranscriptSheet({
  agentId,
  onChanged,
  onClose,
  onDeleted,
  session
}: {
  agentId: string
  onChanged: () => void
  onClose: () => void
  onDeleted: (session: ExternalSession) => void
  session: ExternalSession
}) {
  const { t } = useI18n()
  const c = t.console
  const [messages, setMessages] = useState<null | ExternalMessage[]>(null)
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [renameDraft, setRenameDraft] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)

  useEffect(() => {
    let cancelled = false
    getExternalTranscript(agentId, session.vendor, session.id)
      .then(body => {
        if (!cancelled) {
          setMessages(body?.data?.messages ?? [])
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMessages([])
          setError(c.externalSessionsLoadFailed)
        }
      })

    return () => {
      cancelled = true
    }
  }, [agentId, session.id, session.vendor, c.externalSessionsLoadFailed])

  const send = () => {
    const text = draft.trim()

    if (!text || pending) {
      return
    }

    setPending(true)
    setError('')
    setDraft('')
    setMessages(prev => [...(prev ?? []), { role: 'user', text }])

    sendExternalMessage(agentId, session.vendor, session.id, text)
      .then(body => {
        setMessages(prev => [...(prev ?? []), { role: 'assistant', text: String(body?.data?.reply ?? '') }])
      })
      .catch((reason: unknown) => {
        setError(`${c.externalSessionsSendFailed}${failureText(reason)}`)
      })
      .finally(() => setPending(false))
  }

  const saveRename = () => {
    const title = renameDraft.trim()

    if (!title) {
      return
    }

    patchExternalSession(agentId, session.vendor, session.id, { title })
      .then(() => {
        setRenaming(false)
        onChanged()
      })
      .catch((reason: unknown) => setError(failureText(reason)))
  }

  const toggleArchive = () => {
    patchExternalSession(agentId, session.vendor, session.id, { archived: !session.archived })
      .then(() => {
        onChanged()
        onClose()
      })
      .catch((reason: unknown) => setError(failureText(reason)))
  }

  const remove = () => {
    if (!confirmDelete) {
      setConfirmDelete(true)

      return
    }

    deleteExternalSession(agentId, session.vendor, session.id)
      .then(() => onDeleted(session))
      .catch((reason: unknown) => setError(failureText(reason)))
  }

  return (
    <Dialog onOpenChange={next => { if (!next) {onClose()} }} open>
      <DialogContent className="flex max-h-[80vh] max-w-2xl flex-col">
        <DialogHeader>
          {renaming ? (
            <div className="flex items-center gap-2">
              <input
                autoFocus
                className="min-w-0 flex-1 rounded-md border border-(--ui-stroke-tertiary) bg-transparent px-2 py-1 text-sm outline-none focus:border-(--ui-stroke-secondary)"
                onChange={event => setRenameDraft(event.target.value)}
                onKeyDown={event => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    saveRename()
                  }

                  if (event.key === 'Escape') {
                    event.preventDefault()
                    setRenaming(false)
                  }
                }}
                value={renameDraft}
              />
              <button className="shrink-0 text-[0.75rem] text-(--ui-accent)" onClick={saveRename} type="button">
                {c.externalSessionsSave}
              </button>
              <button
                className="shrink-0 text-[0.75rem] text-(--ui-text-tertiary)"
                onClick={() => setRenaming(false)}
                type="button"
              >
                {c.externalSessionsCancel}
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <DialogTitle className="min-w-0 flex-1 truncate text-sm">{titleOf(session)}</DialogTitle>
              <button
                className="shrink-0 text-[0.72rem] text-(--ui-text-tertiary) hover:text-(--ui-text)"
                onClick={() => {
                  setRenameDraft(titleOf(session))
                  setRenaming(true)
                }}
                type="button"
              >
                {c.externalSessionsRename}
              </button>
              <button
                className="shrink-0 text-[0.72rem] text-(--ui-text-tertiary) hover:text-(--ui-text)"
                onClick={toggleArchive}
                type="button"
              >
                {session.archived ? c.externalSessionsUnarchive : c.externalSessionsArchive}
              </button>
              <button
                className={cn(
                  'shrink-0 text-[0.72rem]',
                  confirmDelete ? 'font-semibold text-(--ui-red)' : 'text-(--ui-text-tertiary) hover:text-(--ui-text)'
                )}
                onClick={remove}
                type="button"
              >
                {confirmDelete ? c.externalSessionsDeleteConfirm : c.externalSessionsDelete}
              </button>
            </div>
          )}
        </DialogHeader>

        <div className="flex min-h-[220px] flex-1 flex-col gap-2 overflow-y-auto py-1">
          {messages === null && (
            <span className="text-[0.75rem] text-(--ui-text-tertiary)">{c.externalSessionsLoading}</span>
          )}
          {messages !== null && messages.length === 0 && (
            <span className="text-[0.75rem] text-(--ui-text-tertiary)">{c.externalSessionsNoMessages}</span>
          )}
          {messages?.map((message, index) => (
            <div
              className={cn(
                'max-w-[85%] whitespace-pre-wrap rounded-lg px-2.5 py-1.5 text-[0.78rem] leading-snug',
                message.role === 'user'
                  ? 'self-end bg-(--ui-accent) text-(--ui-accent-foreground)'
                  : 'self-start bg-(--ui-bg-secondary) text-(--ui-text)'
              )}
              key={`${message.role}-${index}`}
            >
              {message.text}
            </div>
          ))}
          {pending && (
            <span className="self-start text-[0.72rem] text-(--ui-text-tertiary)">{c.externalSessionsSending}</span>
          )}
          {error && <span className="self-start text-[0.72rem] text-(--ui-red)">{error}</span>}
        </div>

        {session.sendable ? (
          <form
            className="flex items-end gap-2 border-t border-(--ui-stroke-tertiary) pt-2"
            onSubmit={event => {
              event.preventDefault()
              send()
            }}
          >
            <textarea
              className="min-h-9 flex-1 resize-none rounded-md border border-(--ui-stroke-tertiary) bg-transparent px-2 py-1 text-[0.8rem] outline-none focus:border-(--ui-stroke-secondary)"
              onChange={event => setDraft(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  send()
                }
              }}
              placeholder={c.externalSessionsInput}
              rows={2}
              value={draft}
            />
            <button
              className="rounded-md bg-(--ui-accent) px-3 py-1.5 text-[0.8rem] text-(--ui-accent-foreground) disabled:opacity-60"
              disabled={pending || !draft.trim()}
              type="submit"
            >
              {pending ? c.externalSessionsSending : c.externalSessionsSend}
            </button>
          </form>
        ) : (
          <div className="border-t border-(--ui-stroke-tertiary) pt-2 text-[0.72rem] text-(--ui-text-tertiary)">
            {c.externalSessionsReadOnly}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** `fetchJson` 对非 2xx 是 reject，消息形如 `400: {...}`；把那句话取出来给人看。 */
function failureText(reason: unknown): string {
  const raw = reason instanceof Error ? reason.message : String(reason ?? '')
  const start = raw.indexOf('{')

  if (start >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(start)) as { error?: unknown }

      if (typeof parsed.error === 'string' && parsed.error) {
        return parsed.error
      }
    } catch {
      // 不是 JSON 就按原文
    }
  }

  return raw.slice(0, 160)
}
