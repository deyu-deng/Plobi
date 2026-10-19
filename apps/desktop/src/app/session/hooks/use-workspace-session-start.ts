import { useCallback, useEffect, useRef } from 'react'

import { useStore } from '@nanostores/react'
import { requestComposerInsert } from '@/app/chat/composer/focus'
import { $startWorkSessionRequest, followActiveSessionCwd, resolveNewSessionCwd } from '@/store/projects'
import { restoreWorktree } from '@/store/layout'
import { setCurrentBranch, setCurrentCwd } from '@/store/session'

interface WorkspaceSessionStartOptions {
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  startFreshSessionDraft: (replaceRoute?: boolean) => void
}

/**
 * WP-DESKTOP-HOOKS-3: start a session anchored to a workspace lane ("+" on a
 * worktree / trunk) and react to composer-initiated "branch off into a new
 * worktree" requests by opening a fresh session prefilled with the task.
 */
export function useWorkspaceSessionStart({ requestGateway, startFreshSessionDraft }: WorkspaceSessionStartOptions) {
  const startSessionInWorkspace = useCallback(
    (path: null | string) => {
      startFreshSessionDraft()

      // A worktree lane carries its own path; the trunk "+" can be path-less (the
      // main checkout is implicit), so fall back to the active project's root
      // instead of no-op'ing on null — that was "+ on main does nothing".
      const target = path?.trim() || resolveNewSessionCwd()

      if (!target) {
        return
      }

      // The next message creates the backend session in $currentCwd, so seed
      // it (and the branch) from the workspace the user clicked the + on.
      setCurrentCwd(target)
      void requestGateway<{ branch?: string; cwd?: string }>('config.get', { key: 'project', cwd: target })
        .then(info => {
          const resolved = info.cwd || target

          setCurrentCwd(resolved)
          setCurrentBranch(info.branch || '')

          // An EXPLICIT target (a worktree/lane path — e.g. just-created via
          // "convert a branch" / "new worktree") drills the sidebar into that
          // project so the new lane is visible at once. Without this, a brand-new
          // worktree session is invisible from the all-projects overview (the
          // live overlay skips `.worktrees` rows, and the session.info cwd-follow
          // only fires on a same-session move, not a fresh session). The
          // path-less trunk "+" keeps the current scope untouched.
          if (path?.trim()) {
            restoreWorktree(resolved)
            void followActiveSessionCwd(resolved)
          }
        })
        .catch(() => undefined)
    },
    [requestGateway, startFreshSessionDraft]
  )

  // Composer "branch off into a new worktree": the composer already created the
  // worktree and cleared its draft; open a fresh session anchored to that tree,
  // then prefill the task that kicked it off. startSessionInWorkspace owns the
  // reset+cwd seed (it runs startFreshSessionDraft, which would otherwise stomp
  // the cwd back to the default), so the prefill is dispatched right after — its
  // deferred event lands once the fresh composer has remounted and rebound.
  const startWorkSessionRequest = useStore($startWorkSessionRequest)
  const lastStartWorkTokenRef = useRef(startWorkSessionRequest?.token ?? 0)

  useEffect(() => {
    if (!startWorkSessionRequest || startWorkSessionRequest.token === lastStartWorkTokenRef.current) {
      return
    }

    lastStartWorkTokenRef.current = startWorkSessionRequest.token
    startSessionInWorkspace(startWorkSessionRequest.path)

    if (startWorkSessionRequest.draft) {
      requestComposerInsert(startWorkSessionRequest.draft, { target: 'main' })
    }
  }, [startSessionInWorkspace, startWorkSessionRequest])

  return { startSessionInWorkspace }
}
