/**
 * Architecture hard gates (ARCH-UI-MASTER §3.5 + §3.6).
 *
 * If an Agent resurrects LeftRail / RightRail / ConsoleHome / Workbench,
 * or adds a second chat runtime owner, this fails.
 *
 * Run: `npm run test:arch`  (node env — no jsdom)
 */
import { readdir, readFile, stat } from 'node:fs/promises'
import { join, relative } from 'node:path'

import { describe, expect, it } from 'vitest'

const desktopRoot = process.cwd()
const consoleDir = join(desktopRoot, 'src', 'app', 'console')
const appDir = join(desktopRoot, 'src', 'app')

const BANNED_BASENAMES = new Set([
  'left-rail.tsx',
  'left-rail.test.tsx',
  'right-rail.tsx',
  'right-rail.test.tsx',
  'right-views.tsx',
  'right-views.test.tsx',
  'subagent-list.tsx',
  'subagent-list.test.tsx',
  'workbench.tsx',
  'workbench.test.tsx'
])

const BANNED_RELATIVE_PATHS = ['console/index.tsx', 'console/index.test.tsx']

async function collectFiles(dir: string, pred: (name: string) => boolean): Promise<string[]> {
  const out: string[] = []

  async function walk(current: string) {
    let entries

    try {
      entries = await readdir(current, { withFileTypes: true })
    } catch {
      return
    }

    for (const entry of entries) {
      const full = join(current, entry.name)

      if (entry.isDirectory()) {
        await walk(full)

        continue
      }

      if (pred(entry.name)) {
        out.push(full)
      }
    }
  }

  await walk(dir)

  return out
}

describe('ARCH-UI-MASTER §3.5 — banned resurrected console shells', () => {
  it('does not recreate LeftRail / RightRail / SubagentList / Workbench files', async () => {
    const hits = await collectFiles(consoleDir, name => BANNED_BASENAMES.has(name))

    expect(hits, `Banned files found:\n${hits.map(h => relative(desktopRoot, h)).join('\n')}`).toEqual([])
  })

  it('does not recreate ConsoleHome at console/index.tsx', async () => {
    const present: string[] = []

    for (const rel of BANNED_RELATIVE_PATHS) {
      const full = join(appDir, rel)

      try {
        await stat(full)
        present.push(rel)
      } catch {
        // missing = good
      }
    }

    expect(present, `Banned paths still present: ${present.join(', ')}`).toEqual([])
  })

  it('does not introduce parallel New*Rail / ConsoleSidebar / console-v2 under app/', async () => {
    const allTsx = await collectFiles(appDir, name => name.endsWith('.tsx') || name.endsWith('.ts'))

    const offenders = allTsx.filter(file => {
      const base = file.replace(/\\/g, '/').split('/').pop() ?? ''
      const rel = relative(appDir, file).replace(/\\/g, '/')

      if (/^New.*Rail/i.test(base) || /^ConsoleSidebar/i.test(base) || /^Simple.*Rail/i.test(base)) {
        return true
      }

      if (rel.includes('console-v2/') || rel.includes('/l1-shell/') || rel.includes('/l2-shell/')) {
        return true
      }

      return false
    })

    expect(offenders, `Parallel shell files:\n${offenders.map(o => relative(desktopRoot, o)).join('\n')}`).toEqual([])
  })
})

describe('ARCH-UI-MASTER §3.6 — single ChatSurface owner', () => {
  /**
   * 2026-11-01 起判据换成「谁都不许自己造」：用户要求点一条外包会话 / 一个专家时
   * 直接复用对话卡片（`chat/conversation-pane.tsx`），于是 console 里出现了第二个
   * 要渲染 Thread 的地方。做法不是让它照抄一遍「缓存 + 仓库 + 增量 store」，
   * 而是把那段固定搭配收进 `@/lib/chat-runtime` 的 `useChatMessageRuntime`——
   * 于是 console 里**一个**运行时所有者都没有，唯一的实现在 lib 里。
   *
   * 这条守卫因此更强：将来谁再在 console 里 import 这个底层钩子，直接红。
   */
  const IMPORTS_RUNTIME = /^\s*import\s+.+useIncrementalExternalStoreRuntime/

  async function runtimeOwners(dir: string): Promise<string[]> {
    const files = await collectFiles(
      dir,
      name => (name.endsWith('.tsx') || name.endsWith('.ts')) && !name.includes('.test.')
    )

    const owners: string[] = []

    for (const file of files) {
      const source = await readFile(file, 'utf8')

      if (source.split('\n').some(line => IMPORTS_RUNTIME.test(line))) {
        owners.push(relative(desktopRoot, file).replace(/\\/g, '/'))
      }
    }

    return owners
  }

  it('console 里没有任何模块自己造运行时', async () => {
    const owners = await runtimeOwners(consoleDir)

    expect(
      owners,
      `console 里又冒出造运行时的地方（该走 @/lib/chat-runtime 的 useChatMessageRuntime）：\n${owners.join('\n')}`
    ).toEqual([])
  })

  it('运行时只有一处实现，就在 lib/chat-runtime', async () => {
    expect(await runtimeOwners(join(desktopRoot, 'src', 'lib'))).toEqual(['src/lib/chat-runtime.ts'])
  })
})
