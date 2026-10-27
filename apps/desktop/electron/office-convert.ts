import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { resolveRequestedPathForIpc } from './hardening'

// Mirrors src/lib/legacy-office.ts for the main process. The renderer owns the
// routing copy (which formats show the conversion card); this copy owns the
// actual conversion target. Keep the two extension tables in sync.
const LEGACY_OFFICE: Record<string, { kind: 'sheet' | 'slides' | 'word'; targetExt: 'docx' | 'pptx' | 'xlsx' }> = {
  doc: { kind: 'word', targetExt: 'docx' },
  docm: { kind: 'word', targetExt: 'docx' },
  dot: { kind: 'word', targetExt: 'docx' },
  dotm: { kind: 'word', targetExt: 'docx' },
  odt: { kind: 'word', targetExt: 'docx' },
  ott: { kind: 'word', targetExt: 'docx' },
  rtf: { kind: 'word', targetExt: 'docx' },
  wps: { kind: 'word', targetExt: 'docx' },
  dps: { kind: 'slides', targetExt: 'pptx' },
  odp: { kind: 'slides', targetExt: 'pptx' },
  otp: { kind: 'slides', targetExt: 'pptx' },
  pot: { kind: 'slides', targetExt: 'pptx' },
  potm: { kind: 'slides', targetExt: 'pptx' },
  ppt: { kind: 'slides', targetExt: 'pptx' },
  pptm: { kind: 'slides', targetExt: 'pptx' },
  et: { kind: 'sheet', targetExt: 'xlsx' },
  ods: { kind: 'sheet', targetExt: 'xlsx' },
  ots: { kind: 'sheet', targetExt: 'xlsx' },
  xls: { kind: 'sheet', targetExt: 'xlsx' },
  xlsm: { kind: 'sheet', targetExt: 'xlsx' },
  xlt: { kind: 'sheet', targetExt: 'xlsx' },
  xltm: { kind: 'sheet', targetExt: 'xlsx' }
}

function legacyOfficeFormat(path: string) {
  const clean = path.split(/[?#]/u)[0]?.toLowerCase() ?? ''
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

        try {
          if (fs.statSync(candidate).isFile()) {
            return candidate
          }
        } catch {
          // Keep looking.
        }
      }
    }
  }

  return null
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

const POWERSHELL_SCRIPT = String.raw`param(
  [Parameter(Mandatory=$true)][string]$InputPath,
  [Parameter(Mandatory=$true)][string]$OutputPath,
  [Parameter(Mandatory=$true)][ValidateSet('word','slides','sheet')][string]$Kind
)
$ErrorActionPreference = 'Stop'
$progIds = switch ($Kind) {
  'word'   { @('Word.Application', 'KWPS.Application') }
  'slides' { @('PowerPoint.Application', 'KWPP.Application') }
  'sheet'  { @('Excel.Application', 'KET.Application') }
}
$app = $null
foreach ($progId in $progIds) {
  try { $app = New-Object -ComObject $progId; break } catch { $app = $null }
}
if ($null -eq $app) { throw "No installed office engine can convert $Kind files." }
try {
  try { $app.Visible = $false } catch {}
  try { $app.DisplayAlerts = 0 } catch {}
  if ($Kind -eq 'word') {
    $doc = $app.Documents.Open($InputPath, $false, $true)
    try { $doc.SaveAs2($OutputPath, 16) } finally { $doc.Close(0) }
  } elseif ($Kind -eq 'slides') {
    $pres = $app.Presentations.Open($InputPath, $true, $false, $false)
    try { $pres.SaveAs($OutputPath, 24) } finally { $pres.Close() }
  } else {
    $book = $app.Workbooks.Open($InputPath, 0, $true)
    try { $book.SaveAs($OutputPath, 51) } finally { $book.Close($false) }
  }
  Write-Output 'OK'
} finally {
  try { $app.Quit() } catch {}
}
`

function runProcess(
  command: string,
  args: string[],
  timeoutMs: number
): Promise<{ code: number; stderr: string; stdout: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
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

async function convertWithLibreOffice(
  inputPath: string,
  outputPath: string,
  targetExt: string,
  cacheDir: string,
  timeoutMs: number
): Promise<LegacyConversionResult> {
  const soffice = findOnPath(['soffice'])

  if (!soffice) {
    return { error: 'libreoffice-missing', ok: false }
  }

  await fs.promises.mkdir(cacheDir, { recursive: true })
  const result = await runProcess(
    soffice,
    ['--headless', '--convert-to', targetExt, '--outdir', cacheDir, inputPath],
    timeoutMs
  )

  if (result.code !== 0) {
    return { error: result.stderr.trim() || 'libreoffice-failed', ok: false }
  }

  const produced = path.join(cacheDir, `${path.basename(inputPath).replace(/\.[^.]+$/u, '')}.${targetExt}`)
  await fs.promises.rename(produced, outputPath)

  return { ok: true, path: outputPath }
}

async function convertWithCom(
  inputPath: string,
  outputPath: string,
  kind: 'sheet' | 'slides' | 'word',
  cacheDir: string,
  timeoutMs: number
): Promise<LegacyConversionResult> {
  if (process.platform !== 'win32') {
    return { error: 'no-office-converter', ok: false }
  }

  await fs.promises.mkdir(cacheDir, { recursive: true })
  const scriptPath = path.join(cacheDir, `convert-${process.pid}-${Date.now()}.ps1`)
  await fs.promises.writeFile(scriptPath, POWERSHELL_SCRIPT, 'utf8')

  try {
    const powershell = process.env.SystemRoot
      ? path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
      : 'powershell.exe'
    const result = await runProcess(
      powershell,
      [
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        scriptPath,
        '-InputPath',
        inputPath,
        '-OutputPath',
        outputPath,
        '-Kind',
        kind
      ],
      timeoutMs
    )

    if (result.code !== 0) {
      return { error: result.stderr.trim() || 'office-conversion-failed', ok: false }
    }

    return { ok: true, path: outputPath }
  } finally {
    await fs.promises.rm(scriptPath, { force: true }).catch(() => {})
  }
}

export async function convertLegacyOffice(
  inputPath: string,
  options: ConvertOptions
): Promise<LegacyConversionResult> {
  const format = legacyOfficeFormat(inputPath)

  if (!format) {
    return { error: 'unsupported-format', ok: false }
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
    ;({ output, reused } = await pickOutput(resolved, format.targetExt))
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'output-error', ok: false }
  }

  if (reused) {
    return { ok: true, path: output, reused: true }
  }

  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const libreOffice = findOnPath(['soffice'])

  if (libreOffice) {
    const result = await convertWithLibreOffice(resolved, output, format.targetExt, options.cacheDir, timeoutMs)

    if (result.ok) {
      return result
    }
  }

  return convertWithCom(resolved, output, format.kind, options.cacheDir, timeoutMs)
}
