import fs from 'node:fs'
import path from 'node:path'

export interface WorkspaceApplication {
  group: 'editor' | 'location' | 'terminal'
  id: string
  name: string
}

export interface DetectedApplication extends WorkspaceApplication {
  args: string[]
  command: string
}

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

function firstExisting(candidates: Array<string | undefined>): string | null {
  for (const candidate of candidates) {
    if (candidate && isFile(candidate)) {
      return candidate
    }
  }

  return null
}

function resolveWindowsExecutable(command: string, relativeExe: string) {
  if (process.platform !== 'win32' || !/\.(cmd|bat)$/iu.test(command)) {
    return command
  }

  // `code.cmd` / `cursor.cmd` are shell wrappers; spawn() cannot launch them
  // without a shell, so point at the GUI executable they wrap.
  const candidate = path.resolve(path.dirname(command), relativeExe)

  return isFile(candidate) ? candidate : command
}

function detectEditor(
  id: string,
  name: string,
  commands: string[],
  candidates: Array<string | undefined>,
  relativeExe: string
): DetectedApplication | null {
  const found = findOnPath(commands) ?? firstExisting(candidates)

  if (!found) {
    return null
  }

  return { args: [], command: resolveWindowsExecutable(found, relativeExe), group: 'editor', id, name }
}

function detectTerminal(): DetectedApplication {
  if (process.platform === 'win32') {
    const wt = findOnPath(['wt'])

    if (wt) {
      return { args: ['-d'], command: wt, group: 'terminal', id: 'terminal', name: 'Windows Terminal' }
    }

    return {
      args: ['/c', 'start', 'cmd.exe', '/K', 'cd', '/d'],
      command: process.env.ComSpec || 'cmd.exe',
      group: 'terminal',
      id: 'terminal',
      name: 'Command Prompt'
    }
  }

  if (process.platform === 'darwin') {
    return { args: ['-a', 'Terminal'], command: 'open', group: 'terminal', id: 'terminal', name: 'Terminal' }
  }

  const emulator = findOnPath(['x-terminal-emulator', 'gnome-terminal', 'konsole', 'xfce4-terminal'])

  return {
    args: [],
    command: emulator ?? 'x-terminal-emulator',
    group: 'terminal',
    id: 'terminal',
    name: 'Terminal'
  }
}

export function detectedApplications(): DetectedApplication[] {
  const editors = [
    detectEditor(
      'vscode',
      'VS Code',
      ['code'],
      [
        process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs', 'Microsoft VS Code', 'Code.exe'),
        process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Microsoft VS Code', 'Code.exe'),
        process.platform === 'darwin' ? '/Applications/Visual Studio Code.app/Contents/MacOS/Electron' : undefined,
        '/usr/bin/code',
        '/usr/share/code/code'
      ],
      '../Code.exe'
    ),
    detectEditor(
      'cursor',
      'Cursor',
      ['cursor'],
      [
        process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs', 'Cursor', 'Cursor.exe'),
        process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Cursor', 'Cursor.exe'),
        process.platform === 'darwin' ? '/Applications/Cursor.app/Contents/MacOS/Cursor' : undefined,
        '/usr/bin/cursor'
      ],
      '../../../Cursor.exe'
    )
  ].filter((item): item is DetectedApplication => Boolean(item))

  const fileManagerName =
    process.platform === 'win32' ? 'File Explorer' : process.platform === 'darwin' ? 'Finder' : 'Files'

  return [
    { args: [], command: '', group: 'location', id: 'file-manager', name: fileManagerName },
    detectTerminal(),
    ...editors
  ]
}

export function listWorkspaceApplications(): WorkspaceApplication[] {
  return detectedApplications().map(({ group, id, name }) => ({ group, id, name }))
}
