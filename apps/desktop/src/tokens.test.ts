/**
 * 死令牌门：用着、却全仓没有定义的颜色令牌，一律红。
 *
 * 为什么要有这扇门（11-08 现核）：
 * 一个没有定义的 `--ui-border` 写进 `border-color`，在**计算值阶段**就非法 ⇒ 回落 `currentColor`
 * ⇒ 描边＝文字颜色，不是发丝线；`--ui-bg-hover` 同理 ⇒ hover 面一直是空的。
 * 这种错屏上肉眼难辨、组件测试也照过（旧用例断的是 `aria-pressed`，不是描边），
 * 只能靠「用着的令牌都得有定义」这条硬判据拦。
 *
 * 口径：
 * · 只管 `--ui-*` 这一族（产品自己的颜色令牌）；`--theme-*`／`--dt-*`／`--radix-*`／
 *   运行时由 JS 注入的那些不在本门范围（它们有别的来路）。
 * · 动态拼出来的名字（`--ui-${…}`）不算：前缀没有定义不是缺陷。
 * · **注释不算用到**（先剥注释再扫）—— 否则写一句「这令牌是死的」就把门自己撞红了。
 * · **已知欠账**列在 `KNOWN_DEBT` 里并注明来路 —— 欠账不藏，但也不假装还完。
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, extname, join } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * 桌面端 `src` 目录的位置：vitest 跑起来时 `import.meta.url` 是 dev server 的 http 地址
 * （jsdom 环境），所以从工作目录往上找，别指望 URL 那条路。
 */
function findSrcDir(): string {
  let dir = process.cwd()

  for (let depth = 0; depth < 6; depth += 1) {
    for (const candidate of [join(dir, 'src'), join(dir, 'apps', 'desktop', 'src')]) {
      if (existsSync(join(candidate, 'app'))) {
        return candidate
      }
    }

    const parent = dirname(dir)

    if (parent === dir) {
      break
    }

    dir = parent
  }

  throw new Error(`找不到桌面端 src 目录（从 ${process.cwd()} 往上找）`)
}

const SRC = findSrcDir()

const TEXT_EXT = new Set(['.ts', '.tsx', '.css', '.html'])
const SKIP_DIRS = new Set(['node_modules', 'dist', '.vite'])

/**
 * 已知欠账（下一刀收，落法见 `Docs/tasks/dead-border-tokens-task.md` 的「下一刀」一节）：
 * · `--ui-surface-2`（12 处）＝ 日历／议程那些小按钮的 hover 面 → `--ui-control-hover-background`
 * · `--ui-text-danger`（8 处）＝ 危险色那几处不显红 → `text-destructive`
 * · `--ui-accent-foreground`／`--ui-text-on-accent`（4 处）＝ 强调色底上的字读不清 → `text-white`
 * · `--ui-surface`／`--ui-surface-secondary`（2 处）＝ 一处日历格底色、一处下拉底色空 → 按角色判
 * · `--ui-background`（2 处）＝ 侧栏那枚头像的 ring 偏移底色空 → `ring-offset-background`
 *   （那一档在派线认领着，本刀不碰）
 */
const KNOWN_DEBT = new Set([
  '--ui-surface',
  '--ui-surface-2',
  '--ui-surface-secondary',
  '--ui-text-danger',
  '--ui-accent-foreground',
  '--ui-text-on-accent',
  '--ui-background'
])

/** CSS 里的定义：`--名: 值`（不含 `var(--名)` 那种用法）。 */
const DEFINE = /(--[A-Za-z0-9_-]+)\s*:[^=]/
/** 用到：`var(--名)`、`(--名)`（Tailwind v4 的任意值写法）、`(--名:`。 */
const USE = /var\(\s*(--[A-Za-z0-9_-]+)|\(\s*(--[A-Za-z0-9_-]+)\s*[:)]/g

/** 剥掉注释（引号里的 `//` 不算）；模板串当代码看，里面的类名照样要扫。 */
function stripComments(text: string): string {
  let out = ''
  let index = 0
  let quote = ''

  while (index < text.length) {
    const char = text[index]
    const next = text[index + 1]

    if (quote) {
      out += char

      if (char === quote && text[index - 1] !== '\\') {
        quote = ''
      }

      index += 1

      continue
    }

    if (char === '"' || char === "'") {
      quote = char
      out += char
      index += 1

      continue
    }

    if (char === '/' && next === '*') {
      const end = text.indexOf('*/', index + 2)
      index = end < 0 ? text.length : end + 2

      continue
    }

    if (char === '/' && next === '/') {
      while (index < text.length && text[index] !== '\n') {
        index += 1
      }

      continue
    }

    out += char
    index += 1
  }

  return out
}

function walk(dir: string): string[] {
  const out: string[] = []

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) {
      continue
    }

    const full = join(dir, entry.name)

    if (entry.isDirectory()) {
      out.push(...walk(full))
    } else if (TEXT_EXT.has(extname(entry.name))) {
      out.push(full)
    }
  }

  return out
}

function isOurFamily(token: string): boolean {
  // `--ui-*` 这一族；前缀式（动态拼出来的）不算。
  return token.startsWith('--ui-') && !token.endsWith('-')
}

function findDeadTokens(): string[] {
  const files = walk(SRC)
  const defined = new Set<string>()
  const used = new Map<string, string[]>()

  for (const file of files) {
    const text = stripComments(readFileSync(file, 'utf8'))

    for (const [index, line] of text.split('\n').entries()) {
      if (file.endsWith('.css')) {
        const def = DEFINE.exec(line)

        if (def && isOurFamily(def[1])) {
          defined.add(def[1])
        }
      }

      for (const match of line.matchAll(USE)) {
        const token = match[1] ?? match[2]

        if (!token || !isOurFamily(token)) {
          continue
        }

        const hits = used.get(token) ?? []
        hits.push(`${file.slice(SRC.length + 1)}:${index + 1}`)
        used.set(token, hits)
      }
    }
  }

  const dead: string[] = []

  for (const [token, hits] of used) {
    if (defined.has(token) || KNOWN_DEBT.has(token)) {
      continue
    }

    dead.push(`${token} —— ${hits.join('、')}｜换仓里真有的那一支（发丝线是 --ui-stroke-*）`)
  }

  return dead.sort()
}

describe('颜色令牌的死活', () => {
  it('用着的 --ui-* 令牌都得有定义', () => {
    const dead = findDeadTokens()

    expect(
      dead,
      dead.length
        ? `用着却全仓没定义的 --ui-* 令牌：\n${dead.join('\n')}\n（要么换成仓里真有的那一支，要么把它写进 KNOWN_DEBT 并注明来路）`
        : ''
    ).toEqual([])
  })
})
