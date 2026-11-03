import { describe, expect, it } from 'vitest'

import { en } from './en'
import { ja } from './ja'
import { zh } from './zh'
import { zhHant } from './zh-hant'

// 两个已经退役的说法不许再出现在人读到的句子里（WP-PROFILE-UI-PURGE 的尾巴，
// 裁定 143④ 撤「分身」这个拟称、裁定 42/178 撤「一家一个 profile」）：
//   · 「分身」——他们全是与总秘书同级、可点可问的智能体；
//   · profile / 配置文件 / 配置档案 / 設定檔 / プロファイル——作为**称呼**已经没有了，
//     名册里那一行才是身份。
// 这条测试扫的是四语目录里的**字符串值**（键名是内部标识，不在屏上）。

const RETIRED = /分身|配置档案|配置文件|設定檔|设置档|プロファイル|\bprofile\b|\bprofiles\b/iu

// 屏上确实要提那个真文件的落点，那几处不是称呼，是文件名。
// 屏上确实要提那个**真文件**的落点（代理池那份 yaml），那几处不是称呼。
const ALLOWED_PATHS = ['settings.network.importPath', 'settings.network.importPool']

const CATALOGS: Array<[string, unknown]> = [
  ['zh', zh],
  ['zh-hant', zhHant],
  ['en', en],
  ['ja', ja],
]

/** `${profile}` 这类插值是**参数名**，不是屏上的词。 */
function stripPlaceholders(value: string): string {
  return value.replace(/\$\{[^}]*\}/g, '')
}

function strings(node: unknown, path = ''): Array<[string, string]> {
  if (typeof node === 'string') {
    return [[path, node]]
  }

  // 目录里不少句子是模板函数（`profile => \`仅当…\``）。屏上的是它**吐出来的字**，
  // 所以只取源码里的字面量，不取参数名——参数叫 profile 是内部标识，不在屏上。
  if (typeof node === 'function') {
    const literals = (node as unknown as string).toString().match(/`[^`]*`|'[^']*'|"[^"]*"/g) ?? []

    return literals.map(literal => [path, literal.slice(1, -1)])
  }

  if (Array.isArray(node)) {
    return node.flatMap((item, index) => strings(item, `${path}[${index}]`))
  }

  if (node && typeof node === 'object') {
    return Object.entries(node as Record<string, unknown>).flatMap(([key, value]) =>
      strings(value, path ? `${path}.${key}` : key)
    )
  }

  return []
}

describe('四语目录里不许留着已退役的称呼', () => {
  it.each(CATALOGS)('%s：没有「分身」，也没有把 profile 当称呼的句子', (_name, catalog) => {
    const hits = strings(catalog)
      .filter(
        ([path, value]) =>
          RETIRED.test(stripPlaceholders(value)) &&
          !ALLOWED_PATHS.some(allowed => path.startsWith(allowed))
      )
      .map(([path, value]) => `${path}: ${value.slice(0, 80)}`)

    expect(hits).toEqual([])
  })

  it('扫描器确实在看：普通句子与模板函数里的字都算，且目录不是空的', () => {
    const planted = {
      appearance: { note: (name: string) => `${name} 配置文件保留各自的主题` },
      console: { line: '这个分身不归你管' },
    }
    const caught = strings(planted).filter(
      ([path, value]) =>
        RETIRED.test(stripPlaceholders(value)) &&
        !ALLOWED_PATHS.some(allowed => path.startsWith(allowed))
    )

    expect(caught.map(([path]) => path)).toEqual(['appearance.note', 'console.line'])

    // 扫到 0 条也算这条测试没牙：四语每份都该有几百句。
    for (const [, catalog] of CATALOGS) {
      expect(strings(catalog).length).toBeGreaterThan(500)
    }
  })
})
