import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { EXPERT_TRADES } from './expert-trades'

/**
 * 名手（L3 专家）的分类表只该有一份内容，两处镜像必须逐字对齐。
 *
 * 2026-11-15 之前这段是散的：后端 `ROLES` 写一份键、`ORDER` 再写一份顺序；前端
 * `expert-roles.ts` 手抄一份 `EXPERT_ROLES`、`store/conversation-view.ts` 又手写一份
 * `ExpertRoleKey` 联合类型。加一类要改三处，漏一处不会报错——只会多出一行永远点不开的
 * 空行（用户 11-04：「根本点击不了」）。现在后端拆成 `TRADES` / `LABELS` / `DUTIES` /
 * `TOOLSETS` 四张同键平表，前端键只在 `shared/expert-trades.ts` 出现一次，由这条测试对表。
 *
 * 全是静态读文件，不启后端——它盯的是"两边写的是不是同一套词"，不是运行时行为。
 */
const here = import.meta.dirname ?? ''
const repoRoot = resolve(here, '../../../..')
const pyPath = resolve(repoRoot, 'plobi/delegation/expert_roles.py')
const sidebarPath = resolve(here, '../app/chat/sidebar/expert-roles.ts')

const py = readFileSync(pyPath, 'utf-8')
const sidebar = readFileSync(sidebarPath, 'utf-8')

/** 抓 `NAME: tuple[...] = (...)` 里的键序列。 */
function tupleOf(source: string, name: string): string[] {
  const line = source.split('\n').find(l => l.startsWith(`${name}:`))
  if (!line) throw new Error(`后端没找到 ${name}`)
  const body = line.slice(line.indexOf('(') + 1, line.lastIndexOf(')'))
  return [...body.matchAll(/"([a-z_]+)"/g)].map(m => m[1])
}

/** 抓 `NAME: dict[...] = {...}` 花括号里的键（保持出现顺序）。 */
function keysOfDict(source: string, name: string): string[] {
  const start = source.indexOf(`${name}: dict`)
  if (start < 0) throw new Error(`后端没找到 ${name}`)
  const open = source.indexOf('{', start)
  let depth = 0
  let end = -1
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1
    if (source[i] === '}') {
      depth -= 1
      if (depth === 0) {
        end = i
        break
      }
    }
  }
  if (end < 0) throw new Error(`${name} 的花括号没闭合`)
  const body = source.slice(open + 1, end)
  return [...body.matchAll(/^\s{4}"([a-z_]+)":/gm)].map(m => m[1])
}

describe('名手分类表：后端与前端是同一套词', () => {
  const backend = tupleOf(py, 'TRADES')

  it('后端 TRADES 非空且顺序稳定（顺序变了左栏就换位，用户会以为少了一类）', () => {
    expect(backend.length).toBeGreaterThanOrEqual(4)
    expect(backend).toEqual(['file', 'browser', 'software', 'computer'])
  })

  it('前端 EXPERT_TRADES 与后端 TRADES 的键与顺序逐字相同', () => {
    expect([...EXPERT_TRADES]).toEqual(backend)
  })

  it('后端四张平表键集合完全一致，缺一格就是运行时 KeyError', () => {
    // 2026-11-16（WP-EXPERT-CAPABILITIES）：`TOOLSETS` 变成了**派生视图**
    // （工具面由能力派生 ⇒ 那一格不再是「字面表」）；承担「四类各自一张表」职责的
    // 换成了 `ROLE_CAPABILITIES`（每个角色默认开哪些能力）。这里盯的是**键集合**，
    // 派生视图另有下面那条断言盯着。
    for (const table of ['LABELS', 'MARKS', 'DUTIES', 'ROLE_CAPABILITIES']) {
      expect(keysOfDict(py, table), `${table} 与 TRADES 不同步`).toEqual(backend)
    }
  })

  it('工具面那一格是派生视图（不许在它里面另写内容）', () => {
    // 派生写法里出现的是 `key: _derive_toolsets(`，不是 `"file": [`——后者说明
    // 有人又开始手抄了（能力项与门类的对应只该在 CAPABILITY_TOOLSETS 一处）。
    expect(py).toContain('TOOLSETS: dict[str, list[str]] = {')
    expect(py).toContain('for key, capabilities in ROLE_CAPABILITIES.items()')
    expect(py).not.toMatch(/TOOLSETS: dict\[str, list\[str\]\] = \{\s*\n\s*"file":/)
  })

  it('ROLES 只是派生视图，不许在它里面另写内容', () => {
    // 派生写法里出现的是 `key: {`，不是 `"file": {`——后者说明有人又开始手抄了。
    const start = py.indexOf('ROLES: dict')
    const body = py.slice(start, py.indexOf('ORDER = TRADES'))
    expect(body).not.toMatch(/"file":/)
    expect(body).toContain('for key in TRADES')
  })

  it('前端渲染表与分类键同序，且每一类都有图标与 i18n 名', () => {
    const look = [...sidebar.matchAll(/^\s{2}([a-z]+): \{ icon: '([^']+)', labelKey: '([^']+)' \},?$/gm)]
    expect(look.map(m => m[1])).toEqual(backend)
    for (const [, key, icon, labelKey] of look) {
      expect(icon, `${key} 缺图标`).not.toBe('')
      const zh = readFileSync(resolve(here, '../i18n/zh.ts'), 'utf-8')
      const en = readFileSync(resolve(here, '../i18n/en.ts'), 'utf-8')
      expect(zh, `zh 缺 ${labelKey}`).toContain(`${labelKey}:`)
      expect(en, `en 缺 ${labelKey}`).toContain(`${labelKey}:`)
    }
  })

  it('前端不再自持第二份键字面量联合（那是第三个真源）', () => {
    expect(sidebar).not.toMatch(/export type ExpertRoleKey = '/)
    expect(sidebar).toContain("from '@/shared/expert-trades'")
  })
})
