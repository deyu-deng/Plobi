/**
 * 「这台机器上有没有新版的桌面端」——查 GitHub Releases，挑本平台那个安装包，
 * 下载它，再交给一个 detached helper 静默装上、把应用拉起来。
 *
 * 为什么自己写，而不是接 `electron-updater`：
 *
 * ① 我们的产物就是 GitHub Release 上的安装包。「下载 + 静默装」＝换掉整个 app
 *    目录，这正是 electron-updater 做的事，我们只是把触发器握在自己手里。
 * ② **后端那一半不在安装包里**。家目录里的 agent checkout 是另一棵树，它得落到
 *    **同一个版本号**上（裁定 208：桌面与 Python 包一条号），所以 helper 除了装
 *    壳，还要跑一次 `install.ps1 -Tag <tag>` 把后端带到那一版。
 * ③ electron-updater 要 build 段配 `publish` 目标、要 CI 产出 `latest.yml`、
 *    macOS 还要签名公证——这三样我们都没有。接一个跑不动的机器，不如自己写一条
 *    能用的路，并在 macOS 上老实说「这一版只能手动装」。
 *
 * 下载走 curl（**继承本进程的 env** ⇒ 走桌面自己的出口代理）：Node 的 https
 * 不认 `http_proxy`，而这台机器直连 github.com 是不通的（校园链路）。
 */

import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export interface ReleaseAsset {
  name: string
  browser_download_url: string
  size: number
}

export interface ReleaseInfo {
  tag: string
  version: string
  assets: ReleaseAsset[]
  htmlUrl: string
}

const RELEASES_API = 'https://api.github.com/repos/deyu-deng/Plobi/releases/latest'

/** The product's own release notes page (the About panel links to it). */
export const RELEASE_NOTES_URL = 'https://github.com/deyu-deng/Plobi/releases'

/**
 * Compare two dotted version strings numerically (`0.3.10` > `0.3.9`).
 * Returns >0 when a is newer, 0 when equal, <0 when a is older.
 */
export function compareVersions(a: string, b: string): number {
  const pa = String(a || '').replace(/^v/, '').split('.')
  const pb = String(b || '').replace(/^v/, '').split('.')
  const len = Math.max(pa.length, pb.length)

  for (let i = 0; i < len; i++) {
    const na = Number.parseInt(pa[i] || '0', 10)
    const nb = Number.parseInt(pb[i] || '0', 10)
    const va = Number.isNaN(na) ? 0 : na
    const vb = Number.isNaN(nb) ? 0 : nb

    if (va !== vb) {
      return va > vb ? 1 : -1
    }
  }

  return 0
}

/**
 * `v0.3.2` -> `0.3.2`; anything that isn't a product version tag -> null.
 *
 * The major is capped at two digits on purpose. The repo inherited CalVer
 * tags (`v2026.10.20`) and those are still published on the Releases page —
 * numerically `2026.10.20` beats every `0.x` version, so without this
 * guard the updater would cheerfully offer "newer" 2026 releases to a
 * 0.3.x install. A product version has a small major; a date-shaped one
 * does not.
 */
export function parseReleaseTag(tag: string): string | null {
  const m = /^v?(\d{1,2})\.(\d+)\.(\d+)$/.exec(String(tag || '').trim())
  return m ? `${m[1]}.${m[2]}.${m[3]}` : null
}

/**
 * The asset that can replace the running install on this platform.
 *
 * Windows: the NSIS `.exe` (silent install swaps the whole app directory).
 * Linux:   the AppImage (self-contained; deb/rpm are for package managers).
 * macOS:   the `.zip` (a `.dmg` would need mounting, and an unsigned
 *          build cannot be swapped in place anyway -- see `canSelfUpdate`).
 */
export function pickInstallerAsset(
  release: ReleaseInfo,
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch
): ReleaseAsset | null {
  const wanted: string[] = []

  if (platform === 'win32') {
    if (arch === 'x64' || arch === 'ia32') {
      wanted.push(`-win-${arch}.exe`)
    }
  } else if (platform === 'linux') {
    if (arch === 'x64') {
      wanted.push('-linux-x86_64.AppImage')
    } else if (arch === 'arm64') {
      wanted.push('-linux-arm64.AppImage')
    }
  } else if (platform === 'darwin') {
    if (arch === 'arm64') {
      wanted.push('-mac-arm64.zip')
    } else if (arch === 'x64') {
      wanted.push('-mac-x64.zip')
    }
  }

  for (const suffix of wanted) {
    const hit = release.assets.find((asset) => asset.name.endsWith(suffix))
    if (hit) {
      return hit
    }
  }

  return null
}

/**
 * macOS refuses to swap an unsigned app in place (no signature ⇒ Gatekeeper
 * and the updater both refuse). We say so instead of pretending.
 */
export function canSelfUpdate(platform: NodeJS.Platform = process.platform): boolean {
  return platform === 'win32' || platform === 'linux'
}

function curlBinary(): string {
  return process.platform === 'win32' ? 'curl.exe' : 'curl'
}

/**
 * Run curl and capture stdout. `--noproxy '*'` is the direct attempt: the
 * desktop's egress proxy is not always up, and api.github.com is reachable
 * directly on some networks. We try the proxy first (the env is inherited),
 * then straight out.
 */
async function curlText(url: string, timeoutMs = 20000): Promise<string | null> {
  const attempts: string[][] = [
    ['--silent', '--show-error', '--location'],
    ['--silent', '--show-error', '--location', '--noproxy', '*']
  ]

  for (const extra of attempts) {
    const out = await new Promise<string | null>((resolve) => {
      const child = spawn(curlBinary(), [...extra, '--max-time', String(Math.ceil(timeoutMs / 1000)), url], {
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore']
      })
      let buf = ''
      child.stdout.on('data', (chunk) => {
        buf += String(chunk)
      })
      const timer = setTimeout(() => {
        try {
          child.kill()
        } catch {
          /* already gone */
        }
      }, timeoutMs)
      child.on('error', () => {
        clearTimeout(timer)
        resolve(null)
      })
      child.on('close', (code) => {
        clearTimeout(timer)
        resolve(code === 0 ? buf : null)
      })
    })

    if (out && out.trim()) {
      return out
    }
  }

  return null
}

/** The newest published release, or null when we can't reach GitHub. */
export async function fetchLatestRelease(): Promise<ReleaseInfo | null> {
  const body = await curlText(RELEASES_API)

  if (!body) {
    return null
  }

  try {
    const parsed = JSON.parse(body)
    const tag = String(parsed?.tag_name || '')
    const version = parseReleaseTag(tag)

    if (!version) {
      return null
    }

    const assets: ReleaseAsset[] = (parsed?.assets || [])
      .filter((asset: any) => asset && asset.name && asset.browser_download_url)
      .map((asset: any) => ({
        name: String(asset.name),
        browser_download_url: String(asset.browser_download_url),
        size: Number(asset.size) || 0
      }))

    return {
      tag,
      version,
      assets,
      htmlUrl: String(parsed?.html_url || RELEASE_NOTES_URL)
    }
  } catch {
    return null
  }
}

/**
 * Download an asset to `dest`. Returns true on success.
 *
 * `--noproxy '*'` fallback included for the same reason as the API call:
 * the egress proxy may be down while the direct route still works.
 */
export async function downloadAsset(url: string, dest: string, timeoutMs = 30 * 60 * 1000): Promise<boolean> {
  const attempts: string[][] = [
    ['--location', '--fail'],
    ['--location', '--fail', '--noproxy', '*']
  ]

  for (const extra of attempts) {
    const ok = await new Promise<boolean>((resolve) => {
      const child = spawn(curlBinary(), [...extra, '--retry', '3', '--retry-delay', '5', '-o', dest, url], {
        windowsHide: true,
        stdio: ['ignore', 'ignore', 'ignore']
      })
      const timer = setTimeout(() => {
        try {
          child.kill()
        } catch {
          /* already gone */
        }
      }, timeoutMs)
      child.on('error', () => {
        clearTimeout(timer)
        resolve(false)
      })
      child.on('close', (code) => {
        clearTimeout(timer)
        resolve(code === 0)
      })
    })

    if (ok && fs.existsSync(dest)) {
      return true
    }
  }

  return false
}

/**
 * The helper that does the actual work AFTER this app has quit:
 *
 *   1. the agent checkout moves to the same tag (so shell and agent share
 *      ONE version number -- 裁定 208);
 *   2. the new installer replaces the shell;
 *   3. the app starts again.
 *
 * It must be detached: the app is quitting, and the venv shim it holds has
 * to be released before step 1 can touch the checkout.
 */
export function writeUpdateHelper(opts: {
  installerPath: string
  installDir: string
  appPath: string
  plobiHome: string
  tag: string
  logPath?: string
}): string {
  const logPath = opts.logPath || path.join(os.tmpdir(), 'plobi-desktop-update.log')
  const agentDir = path.join(opts.plobiHome, 'plobi-agent')
  const installScript = path.join(agentDir, 'scripts', 'install.ps1')
  const isWindows = process.platform === 'win32'

  const lines: string[] = []
  lines.push('@echo off')
  lines.push('setlocal')
  lines.push(`set "PLOBI_LOG=${logPath}"`)
  lines.push('rem --- 1) the agent checkout moves to the same tag ---')
  if (isWindows) {
    lines.push(
      `powershell -ExecutionPolicy Bypass -File "${installScript}" -Tag "${opts.tag}" -PlobiHome "${opts.plobiHome}" -InstallDir "${agentDir}" -NonInteractive >> "%PLOBI_LOG%" 2>&1`
    )
  } else {
    lines.push(
      `sh "${installScript}" --tag "${opts.tag}" --plobi-home "${opts.plobiHome}" --dir "${agentDir}" >> "$PLOBI_LOG" 2>&1`
    )
  }
  lines.push('rem --- 2) the shell is replaced by the new installer ---')
  if (isWindows) {
    lines.push(`"${opts.installerPath}" /S /D=${opts.installDir} >> "%PLOBI_LOG%" 2>&1`)
  } else {
    lines.push(`chmod +x "${opts.installerPath}" >> "$PLOBI_LOG" 2>&1`)
    lines.push(`"${opts.installerPath}" --appimage-extract-and-run >> "$PLOBI_LOG%" 2>&1 &`)
  }
  lines.push('rem --- 3) start the app again ---')
  lines.push(`start "" "${opts.appPath}"`)
  lines.push('endlocal')

  const helperPath = path.join(os.tmpdir(), `plobi-desktop-update-${Date.now()}.${isWindows ? 'cmd' : 'sh'}`)
  fs.writeFileSync(helperPath, lines.join('\n') + '\n', 'utf8')
  return helperPath
}

/**
 * Run the helper detached: it must outlive this app.
 *
 * Returns the helper's pid so the caller can write the "an update is running"
 * marker -- the next launch waits for that pid to be gone before it starts.
 */
export function spawnUpdateHelper(helperPath: string): number | null {
  const isWindows = process.platform === 'win32'
  const child = spawn(isWindows ? 'cmd.exe' : 'sh', isWindows ? ['/c', helperPath] : [helperPath], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true
  })
  child.unref()

  return child.pid ?? null
}
