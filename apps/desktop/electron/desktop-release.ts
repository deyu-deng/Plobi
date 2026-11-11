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
 * The major is capped at two digits on purpose. Every release tag is a
 * product version (`vX.Y.Z`) since the 2026-11-06 renumbering, but a
 * date-shaped tag still sorts above every `0.x` version numerically
 * (`2026.10.20` > `0.3.2`), so without this guard the updater would
 * cheerfully offer "newer" 2026 releases to a 0.3.x install. A product
 * version has a small major; a date-shaped one does not.
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
 * Run curl and capture stdout **plus the HTTP status**.
 *
 * `--noproxy '*'` is the direct attempt: the desktop's egress proxy is not
 * always up, and api.github.com is reachable directly on some networks. We try
 * the proxy first (the env is inherited), then straight out.
 *
 * The status matters: curl exits 0 on a 403/404 body unless `--fail` is given,
 * so without it a rate-limited answer looks like a perfectly good JSON payload
 * — and GitHub's anonymous API budget is 60 requests per hour per IP. We ask
 * for the status so a throttled answer can be told apart from "no release".
 */
async function curlFetch(
  url: string,
  timeoutMs = 20000
): Promise<{ status: number; body: string } | null> {
  const attempts: string[][] = [
    ['--silent', '--show-error', '--location'],
    ['--silent', '--show-error', '--location', '--noproxy', '*']
  ]

  for (const extra of attempts) {
    const out = await new Promise<string | null>((resolve) => {
      const child = spawn(
        curlBinary(),
        [...extra, '--max-time', String(Math.ceil(timeoutMs / 1000)), '--write-out', '\n%{http_code}', url],
        {
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'ignore']
        }
      )

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

    if (!out) {
      continue
    }

    // curl appends the status code on its own last line (the format string
    // starts with a newline), so the body is everything before it.
    const cut = out.lastIndexOf('\n')

    if (cut < 0) {
      continue
    }

    const status = Number.parseInt(out.slice(cut + 1).trim(), 10)

    if (!Number.isFinite(status) || status <= 0) {
      continue
    }

    return { status, body: out.slice(0, cut) }
  }

  return null
}

/** Why there is no release to offer. Internal codes -- they travel to the
 *  renderer as `reason` and are shown on a diagnostic line, never as body copy. */
export type ReleaseLookupReason =
  | 'unreachable'
  | 'rate-limited'
  | 'not-found'
  | 'bad-payload'
  | 'no-version'

export interface ReleaseLookup {
  release: ReleaseInfo | null
  reason: ReleaseLookupReason | null
  /** The HTTP status we got; 0 when the request never made it. */
  status: number
  checkedAt: number
}

/** The one HTTP call this module makes. Injectable so the cache can be tested. */
export type ReleaseFetcher = (url: string) => Promise<{ status: number; body: string } | null>

function parseReleasePayload(body: string): { release: ReleaseInfo | null; reason: ReleaseLookupReason | null } {
  let parsed: any

  try {
    parsed = JSON.parse(body)
  } catch {
    return { release: null, reason: 'bad-payload' }
  }

  const tag = String(parsed?.tag_name || '')
  const version = parseReleaseTag(tag)

  if (!version) {
    return { release: null, reason: 'no-version' }
  }

  const assets: ReleaseAsset[] = (parsed?.assets || [])
    .filter((asset: any) => asset && asset.name && asset.browser_download_url)
    .map((asset: any) => ({
      name: String(asset.name),
      browser_download_url: String(asset.browser_download_url),
      size: Number(asset.size) || 0
    }))

  return {
    release: {
      tag,
      version,
      assets,
      htmlUrl: String(parsed?.html_url || RELEASE_NOTES_URL)
    },
    reason: null
  }
}

export interface ReleaseLookupOptions {
  /** How long a successful answer is reused (ms). */
  ttlMs?: number
  /** How long a FAILED answer is reused (ms) -- short, so a dead proxy is
   *  retried soon, but not on every single focus event. */
  failureTtlMs?: number
  now?: () => number
}

/**
 * The release lookup, with a cache and an in-flight guard.
 *
 * Why the cache: the renderer asks on startup, every 30 minutes, on every
 * window focus, every time the update sheet opens, every time About opens,
 * and once more right before the install. Each of those was a separate curl
 * to api.github.com -- and GitHub's anonymous API budget is 60 requests per
 * hour per IP. Over that budget the answer turns into a 403, which (without
 * `--fail`) would look like a normal JSON body and silently read as "there is
 * no new release". One shared, short-lived answer per machine keeps us well
 * inside the budget and keeps the UI consistent with what it was shown.
 */
export function createReleaseLookup(fetcher: ReleaseFetcher, options: ReleaseLookupOptions = {}) {
  const ttlMs = options.ttlMs ?? 10 * 60 * 1000
  const failureTtlMs = options.failureTtlMs ?? 60 * 1000
  const now = options.now ?? (() => Date.now())

  let cached: { at: number; value: ReleaseLookup } | null = null
  let inFlight: Promise<ReleaseLookup> | null = null

  async function fetchNow(): Promise<ReleaseLookup> {
    const response = await fetcher(RELEASES_API)
    const checkedAt = now()

    if (!response) {
      return { release: null, reason: 'unreachable', status: 0, checkedAt }
    }

    if (response.status === 403 || response.status === 429) {
      return { release: null, reason: 'rate-limited', status: response.status, checkedAt }
    }

    if (response.status === 404) {
      return { release: null, reason: 'not-found', status: response.status, checkedAt }
    }

    if (response.status < 200 || response.status >= 300) {
      return { release: null, reason: 'unreachable', status: response.status, checkedAt }
    }

    const { release, reason } = parseReleasePayload(response.body)

    return { release, reason, status: response.status, checkedAt }
  }

  return {
    async lookup(opts: { force?: boolean } = {}): Promise<ReleaseLookup> {
      const fresh =
        cached !== null && now() - cached.at < (cached.value.release ? ttlMs : failureTtlMs)

      if (!opts.force && fresh) {
        return cached!.value
      }

      // One request at a time: a burst of callers shares the same answer.
      if (inFlight) {
        return inFlight
      }

      inFlight = (async () => {
        const value = await fetchNow()

        cached = { at: value.checkedAt, value }

        return value
      })()

      try {
        return await inFlight
      } finally {
        inFlight = null
      }
    },
    reset(): void {
      cached = null
      inFlight = null
    }
  }
}

/** The lookup the app actually uses. */
export const releaseLookup = createReleaseLookup(async url => curlFetch(url))

/** The newest published release, or null when we can't reach GitHub. */
export async function fetchLatestRelease(): Promise<ReleaseInfo | null> {
  const { release } = await releaseLookup.lookup()

  return release
}

/**
 * Download an asset to `dest`. Returns true on success.
 *
 * `--noproxy '*'` fallback included for the same reason as the API call:
 * the egress proxy may be down while the direct route still works.
 *
 * 这个函数扛的是"整包 400 MB+"的现实（`toolchain` + `wheelhouse` 都在包里）：
 *
 *   - **下完的不重下**：目标文件名固定（`os.tmpdir()/<asset>`)），所以用户点了
 *     更新、安装被占着文件挡回来、再点一次，不该从零再来一遍；
 *   - **断了接着下**：先试 `curl -C -`，服务器不给 Range（返回 200）时 curl 自己
 *     会报错，这时把半截文件删掉再走一次全量——宁可重来，也不要把半截文件当成品；
 *   - **尺寸对不上就是没下成**：CDN 的 HTML 错误页、被掐断的流，尺寸都和
 *     `size` 对不上，凭空返回 true 会让后面的安装器起不来。
 */
export async function downloadAsset(
  url: string,
  dest: string,
  timeoutMs = 30 * 60 * 1000,
  expectedSize = 0
): Promise<boolean> {
  const sizeOf = (): number => {
    try {
      return fs.statSync(dest).size
    } catch {
      return -1
    }
  }
  const drop = (): void => {
    try {
      fs.unlinkSync(dest)
    } catch {
      /* already gone */
    }
  }

  // 上一轮已经拉完的那一个：直接收货。
  if (expectedSize > 0 && isCompleteDownload(sizeOf(), expectedSize)) {
    return true
  }

  let partial = sizeOf()
  if (partial > 0 && expectedSize > 0 && partial > expectedSize) {
    // 比官方尺寸还大 ⇒ 一定是坏的（旧文件 / 合并错），别拿它当断点。
    drop()
    partial = -1
  }

  for (const attempt of planDownloadAttempts({ hasPartial: partial > 0 })) {
    const args: string[] = ['--location', '--fail']
    if (!attempt.direct) {
      args.push('--noproxy', '*')
    }
    if (attempt.resume) {
      args.push('-C', '-')
    }
    args.push('--retry', '3', '--retry-delay', '5', '-o', dest, url)

    const code = await new Promise<number>((resolve) => {
      const child = spawn(curlBinary(), args, {
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
        resolve(-1)
      })
      child.on('close', (exitCode) => {
        clearTimeout(timer)
        resolve(typeof exitCode === 'number' ? exitCode : -1)
      })
    })

    const got = sizeOf()
    const complete = expectedSize > 0 ? isCompleteDownload(got, expectedSize) : got > 0
    if (code === 0 && complete) {
      return true
    }

    if (attempt.resume) {
      // 续传没成：残留下的半截文件会让下一次全量写进同一个文件，先清掉。
      drop()
    }
  }

  return false
}

/** 一次下载尝试的形状：用不用断点、走不走代理。顺序即优先级。 */
export interface DownloadAttempt {
  /** `curl -C -`：从已有文件的末尾接着写。 */
  resume: boolean
  /** 直连（`--noproxy '*'`）；false 表示先按系统代理走。 */
  direct: boolean
}

/**
 * 下载尝试的顺序：**能续就先续**（省的是几百 MB 的流量），续不动再全量；
 * 每一档都留一次绕开代理的机会（egress 代理挂了但直连还通是常态）。
 */
export function planDownloadAttempts(opts: { hasPartial: boolean }): DownloadAttempt[] {
  const tail: DownloadAttempt[] = [
    { resume: false, direct: true },
    { resume: false, direct: false }
  ]
  return opts.hasPartial
    ? [{ resume: true, direct: true }, { resume: true, direct: false }, ...tail]
    : tail
}

/**
 * 「这份文件是不是官方那一份」——只有尺寸能对上才算。
 *
 * `expectedSize <= 0`（release 里没给 size，或旧资产）时无从比对，交给调用方按
 * "文件非空"处理：宁可宽松，也不要因为一个缺失的字段把正常下载判死。
 */
export function isCompleteDownload(actualSize: number, expectedSize: number): boolean {
  if (actualSize <= 0) {
    return false
  }
  if (expectedSize <= 0) {
    return true
  }
  return actualSize === expectedSize
}

/**
 * The helper that does the actual work AFTER this app has quit:
 *
 *   0. wait until this app is really gone -- its files are locked until
 *      then, and installing over a running app half-succeeds;
 *   1. the new installer replaces the shell;
 *   2. the agent is installed out of THAT package:
 *        - the package carries the backend (its runtime source + the locked
 *          dependency wheels): install it from the package -- no git, no
 *          PyPI -- so shell and agent share ONE version number by
 *          construction (裁定 208);
 *        - it does not: move the checkout to the same tag and put the branch
 *          back on that commit (a tag checkout leaves a DETACHED head, and the
 *          next update has no branch to move);
 *   3. the app starts again.
 *
 * It must be detached: the app is quitting, and the venv shim it holds has
 * to be released before step 2 can touch the venv.
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

  // ---- 0) wait for this app to really exit -------------------------------
  lines.push('rem --- 0) wait until this app has really quit (its files are locked until then) ---')

  if (isWindows) {
    lines.push('set /a PL0BI_WAITED=0')
    lines.push(':plobi_wait_exit')
    lines.push('tasklist /FI "IMAGENAME eq Plobi.exe" 2>NUL | find /I "Plobi.exe" >NUL')
    lines.push('if errorlevel 1 goto plobi_app_gone')
    lines.push('set /a PL0BI_WAITED+=2')
    lines.push('if %PL0BI_WAITED% GEQ 120 goto plobi_app_gone')
    lines.push('timeout /t 2 /nobreak >NUL')
    lines.push('goto plobi_wait_exit')
    lines.push(':plobi_app_gone')
  } else {
    lines.push('while pgrep -f "Plobi" >/dev/null 2>&1; do sleep 2; done')
  }

  // ---- 1) the shell is replaced by the new installer ----------------------
  //
  // The shell swap comes FIRST now: a new package may carry the backend (its
  // runtime source + the locked dependency wheels), and the backend sync below
  // has to read them out of the NEW install dir. Order: install the shell,
  // then install the agent out of the package that just landed.
  lines.push('rem --- 1) the shell is replaced by the new installer ---')

  if (isWindows) {
    lines.push(`"${opts.installerPath}" /S /D=${opts.installDir} >> "%PLOBI_LOG%" 2>&1`)
  } else {
    lines.push(`chmod +x "${opts.installerPath}" >> "$PLOBI_LOG" 2>&1`)
    lines.push(`"${opts.installerPath}" --appimage-extract-and-run >> "$PLOBI_LOG%" 2>&1 &`)
  }

  // ---- 2) the agent is installed out of the same package --------------------
  //
  // Two shapes, decided by what the new package actually contains:
  //   * it carries the backend (resources/backend + resources/wheelhouse):
  //     install it FROM THE PACKAGE -- no git, no PyPI -- and the agent is the
  //     same version as the shell by construction;
  //   * it does not (an old package, or a source install): move the checkout
  //     to the release tag, the way we always have.
  lines.push('rem --- 2) the agent is installed from the same package ---')

  const bundledAgent = path.join(opts.installDir, 'resources', 'backend')
  const bundledWheelhouse = path.join(opts.installDir, 'resources', 'wheelhouse')

  if (isWindows) {
    lines.push(`if exist "${path.join(bundledAgent, 'scripts', 'install.ps1')}" (`)
    lines.push(`  rem --- 2a) bundled backend: install it out of the package, offline ---`)
    // Two stages, not the whole script: `repository` copies the packaged source,
    // `dependencies` installs the locked wheels from the packaged wheelhouse
    // (offline). Skipping the venv stage keeps the venv that is already there
    // (its Scripts\*.exe carry absolute paths -- recreating it costs minutes),
    // and `uv sync` reconciles it with the new version's lockfile. The node
    // stage is skipped too: the desktop's own node deps travel in the package,
    // and npm would need the network.
    lines.push(
      `  powershell -ExecutionPolicy Bypass -File "${path.join(bundledAgent, 'scripts', 'install.ps1')}" -Stage repository -BundleDir "${bundledAgent}" -Wheelhouse "${bundledWheelhouse}" -PlobiHome "${opts.plobiHome}" -InstallDir "${agentDir}" -NonInteractive >> "%PLOBI_LOG%" 2>&1`
    )
    lines.push(
      `  powershell -ExecutionPolicy Bypass -File "${path.join(bundledAgent, 'scripts', 'install.ps1')}" -Stage dependencies -BundleDir "${bundledAgent}" -Wheelhouse "${bundledWheelhouse}" -PlobiHome "${opts.plobiHome}" -InstallDir "${agentDir}" -NonInteractive >> "%PLOBI_LOG%" 2>&1`
    )
    lines.push(`) else (`)
    lines.push(`  rem --- 2b) source install: move the checkout to the same tag ---`)
    lines.push(
      `  powershell -ExecutionPolicy Bypass -File "${installScript}" -Tag "${opts.tag}" -PlobiHome "${opts.plobiHome}" -InstallDir "${agentDir}" -NonInteractive >> "%PLOBI_LOG%" 2>&1`
    )
    lines.push(
      `  rem --- 2c) the tag checkout leaves a detached HEAD: point main at the release commit and attach to it ---`
    )
    lines.push(`  git -C "${agentDir}" branch -f main "refs/tags/${opts.tag}" >> "%PLOBI_LOG%" 2>&1`)
    lines.push(`  git -C "${agentDir}" checkout main >> "%PLOBI_LOG%" 2>&1`)
    lines.push(`)`)
  } else {
    // Same two shapes as the Windows branch. The POSIX stage names differ:
    // install.sh calls the dependency stage `python-deps`.
    lines.push(`if [ -d "${bundledAgent}" ]; then`)
    lines.push(`  rem --- 2a) bundled backend: install it out of the package, offline ---`)
    lines.push(
      `  sh "${path.join(bundledAgent, 'scripts', 'install.sh')}" --stage repository --bundle-dir "${bundledAgent}" --wheelhouse "${bundledWheelhouse}" --plobi-home "${opts.plobiHome}" --dir "${agentDir}" --non-interactive >> "$PLOBI_LOG" 2>&1`
    )
    lines.push(
      `  sh "${path.join(bundledAgent, 'scripts', 'install.sh')}" --stage python-deps --bundle-dir "${bundledAgent}" --wheelhouse "${bundledWheelhouse}" --plobi-home "${opts.plobiHome}" --dir "${agentDir}" --non-interactive >> "$PLOBI_LOG" 2>&1`
    )
    lines.push(`else`)
    lines.push(`  rem --- 2b) source install: move the checkout to the same tag ---`)
    lines.push(
      `  sh "${installScript}" --tag "${opts.tag}" --plobi-home "${opts.plobiHome}" --dir "${agentDir}" >> "$PLOBI_LOG" 2>&1`
    )
    lines.push(`  git -C "${agentDir}" branch -f main "refs/tags/${opts.tag}" >> "$PLOBI_LOG" 2>&1`)
    lines.push(`  git -C "${agentDir}" checkout main >> "$PLOBI_LOG" 2>&1`)
    lines.push(`fi`)
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
