import { spawn } from 'node:child_process'

import { shell } from 'electron'

import { resolveDirectoryForIpc } from './hardening'
import { detectedApplications, listWorkspaceApplications } from './workspace-apps'

export { listWorkspaceApplications }

export async function openWorkspaceWithApplication(root: string, appId: string) {
  const { resolvedPath } = await resolveDirectoryForIpc(root, { purpose: 'Open workspace' })
  const application = detectedApplications().find(item => item.id === appId) ?? detectedApplications()[0]

  if (!application) {
    return { error: 'no-application', ok: false }
  }

  if (application.id === 'file-manager') {
    const error = await shell.openPath(resolvedPath)

    return error ? { error, ok: false } : { ok: true }
  }

  try {
    const args = application.id === 'terminal' ? [...application.args, resolvedPath] : [resolvedPath]
    const child = spawn(application.command, args, {
      detached: true,
      stdio: 'ignore',
      windowsHide: false
    })
    child.unref()

    return { ok: true }
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'launch-failed', ok: false }
  }
}
