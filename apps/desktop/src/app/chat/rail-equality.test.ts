import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { normalizeAgentCategory } from './sidebar/agent-groups'

/**
 * `WP-RAIL-AGENT-EQUAL` 的两条钉子（裁定 146）。
 *
 * 都写成**关系**，不写快照：一条钉"内部拟称不许出现在面上"，一条钉
 * "后端品类表里的每一个值，界面上都说得出是什么"——后者就是当年课程那一栏
 * 静默折进项目/管家那一族的形状（`agent-groups.ts` 顶上那段注释已经认过这个老 bug）。
 */

const LOCALES = ['en', 'zh', 'zh-hant', 'ja'] as const

const read = (rel: string): string => readFileSync(new URL(rel, import.meta.url), 'utf-8')

function agentCategoryKeys(source: string): string[] {
  const block = /agentCategories:\s*\{([\s\S]*?)\n\s{4}\}/.exec(source)

  if (!block) {
    throw new Error('读不到 agentCategories 那块（文件结构变了，先看一眼再改这条测试）')
  }

  return [...block[1].matchAll(/^\s*([A-Za-z_][\w-]*)\s*:/gm)].map(m => m[1])
}

describe('左栏平等（裁定 146）', () => {
  it('四份语言文件里都不许出现「分身」这个内部拟称', () => {
    for (const locale of LOCALES) {
      const source = read(`../../i18n/${locale}.ts`)

      expect(source, `i18n/${locale}.ts 面上文案里还有内部拟称`).not.toMatch(/分身/)
    }
  })

  it('后端品类表的每个值，界面上都有名字；未知值不静默折进别的栏', () => {
    const registry = read('../../../../../plobi/agents/registry.py')
    const tuple = /AGENT_CATEGORIES[^=]*=\s*\(([\s\S]*?)\)/.exec(registry)

    expect(tuple, '读不到 AGENT_CATEGORIES').not.toBeNull()

    const backend = [...tuple![1].matchAll(/"([\w-]+)"/g)].map(m => m[1])

    expect(backend.length).toBeGreaterThan(0)

    for (const locale of LOCALES) {
      const labels = agentCategoryKeys(read(`../../i18n/${locale}.ts`))

      for (const category of backend) {
        expect(labels, `${locale} 里品类 ${category} 没有标签（会露出裸键）`).toContain(category)
      }
    }

    // 未知品类原样留着（它得能被解释成"一个新栏"），只有空值才归管家。
    expect(normalizeAgentCategory('lab-notes')).toBe('lab-notes')
    expect(normalizeAgentCategory('   ')).toBe('butler')
  })
})
