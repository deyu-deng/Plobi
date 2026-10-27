import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { resolveRequestedPathForIpc } from './hardening'

// Mirrors src/lib/legacy-office.ts for the main process. The renderer owns the
// routing copy (which formats show the conversion card); this copy owns the
// actual conversion target. Keep the two extension tables in sync.
const LEGACY_OFFICE: Record<string, 'docx' | 'pptx' | 'xlsx'> = {
  doc: 'docx',
  docm: 'docx',
  dot: 'docx',
  dotm: 'docx',
  odt: 'docx',
  ott: 'docx',
  rtf: 'docx',
  wps: 'docx',
  dps: 'pptx',
  odp: 'pptx',
  otp: 'pptx',
  pot: 'pptx',
  potm: 'pptx',
  ppt: 'pptx',
  pptm: 'pptx',
  et: 'xlsx',
  ods: 'xlsx',
  ots: 'xlsx',
  xls: 'xlsx',
  xlsm: 'xlsx',
  xlt: 'xlsx',
  xltm: 'xlsx'
}

function legacyOfficeTarget(inputPath: string) {
  const clean = inputPath.split(/[?#]/u)[0]?.toLowerCase() ?? ''
  const dot = clean.lastIndexOf('.')
  const ext = dot >= 0 ? clean.slice(dot + 1) : ''

  return LEGACY_OFFICE[ext] ?? null
}

export interface LegacyConversionResult {
  error?: string
  ok: boolean
  path?: string
  reused?: boolean
}

interface ConvertOptions {
  cacheDir: string
  timeoutMs?: number
}

const DEFAULT_TIMEOUT_MS = 180_000

function isFile(candidate: string) {
  try {
    return fs.statSync(candidate).isFile()
  } catch {
    return false
  }
}

function findOnPath(names: string[]): string | null {
  const directories = (process.env.PATH || '').split(path.delimiter).filter(Boolean)
  const extensions =
    process.platform === 'win32'
      ? (process.env.PATHEXT || '.EXE;.CMD;.BAT').split(';').filter(Boolean)
      : ['']

  for (const directory of directories) {
    for (const name of names) {
      for (const extension of extensions) {
        const candidate = path.join(directory, `${name}${extension}`)

        if (isFile(candidate)) {
          return candidate
        }
      }
    }
  }

  return null
}

const LIBREOFFICE_PATHS =
  process.platform === 'win32'
    ? [
        'D:\\Software\\LibreOffice\\program\\soffice.exe',
        'D:\\Portables\\gui\\LibreOffice\\program\\soffice.exe',
        'D:\\Portables\\cli\\libreoffice\\program\\soffice.exe',
        'C:\\Program Files\\LibreOffice\\program\\soffice.exe',
        'C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe'
      ]
    : process.platform === 'darwin'
      ? ['/Applications/LibreOffice.app/Contents/MacOS/soffice']
      : [
          '/usr/bin/soffice',
          '/usr/bin/libreoffice',
          '/usr/lib/libreoffice/program/soffice',
          '/opt/libreoffice/program/soffice',
          '/snap/bin/libreoffice'
        ]

function findLibreOffice(): string | null {
  const override = process.env.PLOBI_LIBREOFFICE_PATH?.trim()

  if (override) {
    const binary = fs.existsSync(override) && fs.statSync(override).isDirectory()
      ? path.join(override, 'program', process.platform === 'win32' ? 'soffice.exe' : 'soffice')
      : override

    if (isFile(binary)) {
      return binary
    }
  }

  for (const candidate of LIBREOFFICE_PATHS) {
    if (isFile(candidate)) {
      return candidate
    }
  }

  return findOnPath(['soffice', 'libreoffice'])
}

function outputCandidates(inputPath: string, targetExt: string) {
  const stem = inputPath.replace(/\.[^.\\/]+$/u, '')
  const base = `${stem}.${targetExt}`
  const candidates = [base]

  for (let index = 1; index <= 20; index += 1) {
    candidates.push(`${stem}-converted${index > 1 ? `-${index}` : ''}.${targetExt}`)
  }

  return candidates
}

async function pickOutput(inputPath: string, targetExt: string): Promise<{ output: string; reused: boolean }> {
  const inputStat = await fs.promises.stat(inputPath)
  const candidates = outputCandidates(inputPath, targetExt)
  const first = candidates[0]

  try {
    const existing = await fs.promises.stat(first)

    if (existing.mtimeMs >= inputStat.mtimeMs) {
      return { output: first, reused: true }
    }
  } catch {
    // No converted sibling yet.
  }

  for (const candidate of candidates) {
    try {
      await fs.promises.stat(candidate)
    } catch {
      return { output: candidate, reused: false }
    }
  }

  throw new Error('too many converted copies beside this file')
}

function runProcess(
  command: string,
  args: string[],
  timeoutMs: number
): Promise<{ code: number; stderr: string; stdout: string }> {
  return new Promise((resolve, reject) => {
    const env = { ...process.env }

    // LibreOffice ships its own Python; an inherited PYTHONHOME/PYTHONPATH from
    // a dev venv makes it print prefix errors and can break conversions.
    delete env.PYTHONHOME
    delete env.PYTHONPATH

    const child = spawn(command, args, { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
    let stdout = ''
    let stderr = ''
    let settled = false
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true
        child.kill()
        reject(new Error('conversion timed out'))
      }
    }, timeoutMs)

    child.stdout?.on('data', chunk => {
      stdout += String(chunk)
    })
    child.stderr?.on('data', chunk => {
      stderr += String(chunk)
    })
    child.on('error', error => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        reject(error)
      }
    })
    child.on('exit', code => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        resolve({ code: code ?? 1, stderr, stdout })
      }
    })
  })
}

// LibreOffice serializes on a single user profile; overlapping conversions from
// two tabs would otherwise race on that profile lock.
let conversionQueue: Promise<unknown> = Promise.resolve()

function enqueue<T>(task: () => Promise<T>): Promise<T> {
  const run = conversionQueue.then(task, task)
  conversionQueue = run.catch(() => {})

  return run
}

async function convertWithLibreOffice(
  soffice: string,
  inputPath: string,
  outputPath: string,
  targetExt: string,
  cacheDir: string,
  timeoutMs: number
): Promise<LegacyConversionResult> {
  await fs.promises.mkdir(cacheDir, { recursive: true })

  const profileDir = path.join(cacheDir, 'profile')
  const produced = path.join(cacheDir, `${path.basename(inputPath).replace(/\.[^.]+$/u, '')}.${targetExt}`)

  await fs.promises.rm(produced, { force: true })

  const result = await runProcess(
    soffice,
    [
      '--headless',
      '--norestore',
      '--nodefault',
      '--nolockcheck',
      `-env:UserInstallation=${pathToFileURL(profileDir).href}`,
      '--convert-to',
      targetExt,
      '--outdir',
      cacheDir,
      inputPath
    ],
    timeoutMs
  )

  if (result.code !== 0) {
    return { error: result.stderr.trim() || 'libreoffice-failed', ok: false }
  }

  let producedStat: fs.Stats

  try {
    producedStat = await fs.promises.stat(produced)
  } catch {
    // LibreOffice exits 0 even when the filter refuses the document.
    return { error: result.stdout.trim() || result.stderr.trim() || 'libreoffice-failed', ok: false }
  }

  if (!producedStat.isFile() || producedStat.size === 0) {
    return { error: 'libreoffice-produced-empty-file', ok: false }
  }

  await fs.promises.rm(outputPath, { force: true })
  await fs.promises.rename(produced, outputPath)

  return { ok: true, path: outputPath }
}

export async function convertLegacyOffice(
  inputPath: string,
  options: ConvertOptions
): Promise<LegacyConversionResult> {
  const targetExt = legacyOfficeTarget(inputPath)

  if (!targetExt) {
    return { error: 'unsupported-format', ok: false }
  }

  const soffice = findLibreOffice()

  if (!soffice) {
    return { error: 'libreoffice-missing', ok: false }
  }

  let resolved: string

  try {
    resolved = resolveRequestedPathForIpc(inputPath, { purpose: 'Legacy office conversion' })
    const stat = await fs.promises.stat(resolved)

    if (!stat.isFile()) {
      return { error: 'not-a-file', ok: false }
    }
  } catch (error) {
    return { error: (error as NodeJS.ErrnoException)?.code || 'read-error', ok: false }
  }

  let output: string
  let reused: boolean

  try {
    ;({ output, reused } = await pickOutput(resolved, targetExt))
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'output-error', ok: false }
  }

  if (reused) {
    return { ok: true, path: output, reused: true }
  }

  try {
    return await enqueue(() =>
      convertWithLibreOffice(
        soffice,
        resolved,
        output,
        targetExt,
        options.cacheDir,
        options.timeoutMs ?? DEFAULT_TIMEOUT_MS
      )
    )
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'conversion-failed', ok: false }
  }
}
