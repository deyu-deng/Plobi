import { useEffect, useState } from 'react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { Codicon } from '@/components/ui/codicon'
import { useI18n, type Translations } from '@/i18n'
import type { PlobiWorkspaceApplication } from '@/global'
import {
  isDesktopFsRemoteMode,
  listDesktopWorkspaceApplications,
  openDesktopWorkspaceWith
} from '@/lib/desktop-fs'
import { persistString, storedString } from '@/lib/storage'
import { notifyError } from '@/store/notifications'

const PREFERENCE_KEY = 'plobi.desktop.workspaceOpenApp'

function applicationIcon(application: PlobiWorkspaceApplication | undefined) {
  if (!application) {
    return 'folder'
  }

  if (application.id === 'terminal') {
    return 'terminal'
  }

  return application.id === 'file-manager' ? 'window' : 'code'
}

function applicationLabel(application: PlobiWorkspaceApplication | undefined, labels: Translations['rightSidebar']) {
  if (!application) {
    return labels.openInFileManager
  }

  if (application.id === 'file-manager') {
    return labels.openInFileManager
  }

  if (application.id === 'terminal') {
    return labels.openInTerminal
  }

  return application.name
}

export function WorkspaceOpenWith({ cwd }: { cwd: string }) {
  const { t } = useI18n()
  const r = t.rightSidebar
  const remote = isDesktopFsRemoteMode()
  const [applications, setApplications] = useState<PlobiWorkspaceApplication[]>([])
  const [selectedId, setSelectedId] = useState(() => storedString(PREFERENCE_KEY) ?? 'file-manager')
  const [opening, setOpening] = useState(false)

  useEffect(() => {
    if (!cwd || remote) {
      return
    }

    let cancelled = false

    void listDesktopWorkspaceApplications().then(next => {
      if (cancelled || next.length === 0) {
        return
      }

      setApplications(next)
      setSelectedId(current => (next.some(item => item.id === current) ? current : next[0].id))
    })

    return () => {
      cancelled = true
    }
  }, [cwd, remote])

  if (!cwd || remote) {
    return null
  }

  const selected = applications.find(item => item.id === selectedId) ?? applications[0]
  const label = applicationLabel(selected, r)

  const open = async (application: PlobiWorkspaceApplication | undefined) => {
    if (!application || opening) {
      return
    }

    setOpening(true)

    try {
      const result = await openDesktopWorkspaceWith(cwd, application.id)

      if (!result.ok) {
        notifyError(new Error(result.error ?? r.openWorkspaceFailed), r.openWorkspaceFailed)
      } else {
        setSelectedId(application.id)
        persistString(PREFERENCE_KEY, application.id)
      }
    } catch (error) {
      notifyError(error, r.openWorkspaceFailed)
    } finally {
      setOpening(false)
    }
  }

  return (
    <div className="relative flex h-6 shrink-0 items-center overflow-hidden rounded-md border border-(--ui-stroke-tertiary)">
      <button
        aria-label={r.openWith(label)}
        className="grid h-6 w-7 place-items-center text-(--ui-text-secondary) transition-colors hover:bg-(--ui-row-hover-background) hover:text-foreground disabled:opacity-50"
        disabled={opening || !selected}
        onClick={() => void open(selected)}
        title={r.openWith(label)}
        type="button"
      >
        <Codicon
          name={opening ? 'loading' : applicationIcon(selected)}
          size="0.8125rem"
          spinning={opening}
        />
      </button>
      <span aria-hidden className="h-3.5 w-px bg-(--ui-stroke-tertiary)" />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={r.chooseOpenWith}
            className="grid h-6 w-5 place-items-center text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-row-hover-background) hover:text-foreground"
            type="button"
          >
            <Codicon name="chevron-down" size="0.7rem" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          {applications.length === 0 ? (
            <DropdownMenuItem disabled>{r.workspaceAppsLoading}</DropdownMenuItem>
          ) : (
            applications.map(application => (
              <DropdownMenuItem key={application.id} onSelect={() => void open(application)}>
                <Codicon name={applicationIcon(application)} size="0.8125rem" />
                <span className="min-w-0 flex-1 truncate">
                  {applicationLabel(application, r)}
                </span>
                {application.id === selected?.id ? (
                  <Codicon className="ml-auto" name="check" size="0.75rem" />
                ) : null}
              </DropdownMenuItem>
            ))
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
