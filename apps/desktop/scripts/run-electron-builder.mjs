// Resolve electronDist at runtime (#38673, #47917): electron-builder 26.8.x can
// re-unpack a broken Electron.app; reusing the installed dist dodges that.
// npm workspace hoisting is non-deterministic — require.resolve finds electron
// wherever it landed. Dist present → -c.electronDist=<abs>/dist; absent → let
// electron-builder fetch via @electron/get (electronVersion + ELECTRON_MIRROR).

import fs from "node:fs"
import path from "node:path"
import { spawnSync } from "node:child_process"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"

const require = createRequire(import.meta.url)

function electronDistDir() {
  try {
    return path.join(path.dirname(require.resolve("electron/package.json")), "dist")
  } catch {
    return null
  }
}

function distBinary(dist) {
  if (process.platform === "darwin") {
    return path.join(dist, "Electron.app", "Contents", "MacOS", "Electron")
  }
  if (process.platform === "win32") {
    return path.join(dist, "electron.exe")
  }
  return path.join(dist, "electron")
}

function electronBuilderCli() {
  const pkgJson = require.resolve("electron-builder/package.json")
  const bin = require(pkgJson).bin
  const rel = typeof bin === "string" ? bin : bin["electron-builder"]
  return path.join(path.dirname(pkgJson), rel)
}

// ── Preflight: refuse to package a dirty worktree ────────────────────────────
// A build is a published artefact, and its provenance has to be a commit — not
// "whatever was open in the editor". Without this the installer can carry
// half-finished source that exists nowhere in git, which is exactly how the
// 2026-11-06 0.3.2 package shipped code that did not match the repo. Untracked
// files count: a new file that was never added is source too.
//
// PLOBI_ALLOW_DIRTY_BUILD=1 is the local escape hatch for iteration builds; it
// leaves a loud warning behind so a dirty artefact can never be mistaken for a
// release.
function repoRoot() {
  // apps/desktop/scripts -> apps/desktop -> apps -> <root>
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..")
}

function assertCleanWorktree() {
  if (process.env.PLOBI_ALLOW_DIRTY_BUILD === "1") {
    console.warn(
      "[run-electron-builder] PLOBI_ALLOW_DIRTY_BUILD=1 — skipping the worktree check. " +
        "This build is NOT releasable: its source is not in git."
    )
    return
  }

  const root = repoRoot()
  const status = spawnSync("git", ["status", "--porcelain"], { cwd: root, encoding: "utf8" })

  if (status.error) {
    // No git (source tarball, minimal container): there is nothing to compare
    // against, so say so instead of pretending the tree is clean.
    console.warn(
      `[run-electron-builder] cannot verify worktree cleanliness (${status.error.message}); continuing.`
    )
    return
  }

  if (status.status !== 0) {
    console.error(
      `[run-electron-builder] git status failed in ${root}:\n${(status.stderr || "").trim()}`
    )
    process.exit(1)
  }

  const dirty = (status.stdout || "").trim()
  if (dirty) {
    const lines = dirty.split(/\r?\n/)
    console.error(
      "[run-electron-builder] 工作区有未提交改动，禁止打包 (worktree is dirty, refusing to package).\n" +
        "Commit, stash, or revert these first — a package must point at a commit:\n\n" +
        lines
          .slice(0, 40)
          .map(line => `  ${line}`)
          .join("\n") +
        (lines.length > 40 ? `\n  … ${lines.length - 40} more` : "") +
        "\n\nFor a throwaway local build: PLOBI_ALLOW_DIRTY_BUILD=1 (never for a release)."
    )
    process.exit(1)
  }
}

assertCleanWorktree()

const dist = electronDistDir()
const args = []
if (dist && fs.existsSync(distBinary(dist))) {
  args.push(`-c.electronDist=${dist}`)
} else {
  console.warn(
    "[run-electron-builder] no local electron dist; electron-builder will fetch " +
      "via @electron/get (electronVersion + ELECTRON_MIRROR)."
  )
}
args.push(...process.argv.slice(2))

const result = spawnSync(process.execPath, [electronBuilderCli(), ...args], {
  stdio: "inherit",
})
if (result.error) {
  console.error(`[run-electron-builder] spawn failed: ${result.error.message}`)
  process.exit(1)
}
process.exit(result.status == null ? 1 : result.status)
