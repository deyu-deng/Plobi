import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { describe, it } from 'node:test'

import { detectedApplications, listWorkspaceApplications } from './workspace-apps'

describe('workspace applications', () => {
  it('always offers the system file manager and a terminal', () => {
    const ids = listWorkspaceApplications().map(application => application.id)

    assert.ok(ids.includes('file-manager'))
    assert.ok(ids.includes('terminal'))
  })

  it('does not list duplicate application ids', () => {
    const ids = listWorkspaceApplications().map(application => application.id)

    assert.equal(new Set(ids).size, ids.length)
  })

  it('only exposes applications whose launcher exists', () => {
    for (const application of detectedApplications()) {
      if (application.id === 'file-manager' || !path.isAbsolute(application.command)) {
        // Command-less system entries (file manager) and PATH-only fallbacks
        // (e.g. no terminal emulator installed) are allowed.
        continue
      }

      assert.ok(fs.existsSync(application.command), `${application.id}: ${application.command}`)
    }
  })

  it('never hands spawn() a Windows .cmd shell wrapper', { skip: process.platform !== 'win32' }, () => {
    for (const application of detectedApplications()) {
      if (application.group === 'editor') {
        assert.doesNotMatch(application.command, /\.(cmd|bat)$/iu)
      }
    }
  })
})
