import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import type { Agent } from '../console/types'

import { AGENT_CATEGORY_PREFERENCE, groupAgentsByCategory, normalizeAgentCategory } from './sidebar/agent-groups'
import { creatableCategoryOptions } from './sidebar/create-agent'

/**
 * `WP-RAIL-AGENT-EQUAL` 的钉子（裁定 146）。
 *
 * 都写成**关系**，不写快照：一条钉"内部拟称不许出现在面上"，一条钉
 * "后端品类表里的每一个值，界面上都说得出是什么"——后者就是当年课程那一栏
 * 静默折进项目/管家那一族的形状（`agent-groups.ts` 顶上那段注释已经认过这个老 bug）。
 *
 * 后面几条是这一波补的：空品类不许成栏、建册的品类表不许漏类、五种行吃同一套
 * chrome、日程只许一个入口。
 */

const LOCALES = ['en', 'zh', 'zh-hant', 'ja'] as const

const read = (rel: string): string => readFileSync(new URL(rel, import.meta.url), 'utf-8')

const railAgent = (category: string, name: string): Agent => ({ category, name }) as unknown as Agent

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

  it('名册里没有条目的品类，左栏不许出现一栏空的', () => {
    // 这台现核：projects 10 / courses 11 / research 2 / butler 1，events 0 条。
    // 判据不是"把 events 这一栏删了"，而是"非空才成栏"——哪天第一条确认事件落进
    // 名册（裁定 19 那条路径），这一栏自己就出来了，不必再改客户端。
    const groups = groupAgentsByCategory([
      railAgent('projects', 'Aura'),
      railAgent('courses', '高等数学')
    ])

    expect(groups.map(group => group.category)).not.toContain('events')
    expect(groups.map(group => group.category)).toEqual(['projects', 'courses'])
  })

  it('建册对话框的品类表不许漏类：声明顺序 + 名册里已有的那些', () => {
    const options = creatableCategoryOptions(['projects', 'lab-notes'])

    for (const declared of AGENT_CATEGORY_PREFERENCE) {
      expect(options, `声明过的品类 ${declared} 在建册里选不到`).toContain(declared)
    }

    // 后端/名册冒出来的一类，不必有人回来补一行才看得见（courses 就是这么栽的）。
    expect(options).toContain('lab-notes')
  })

  it('五种行吃同一套 chrome：分身栏不许自带行样式，状态点不许各挑尺寸', () => {
    const rail = read('./sidebar/agent-category-group.tsx')
    const session = read('./sidebar/session-row.tsx')

    expect(rail, '分身栏不再从 chrome 取行件').toContain("from './chrome'")
    expect(rail, '分身栏又长出一份私有行样式').not.toMatch(/const AGENT_ROW/)

    for (const [name, source] of [['分身栏', rail], ['会话栏', session]] as const) {
      expect(source, `${name} 的状态点没走共用尺寸`).toMatch(/SIDEBAR_ROW_DOT_SIZE/)
      expect(source, `${name} 还写死了点的尺寸`).not.toMatch(/'size-[12] '|size-[12] rounded/)
    }
  })

  it('日程只许一个入口：完整日程页的导航件全应用就状态栏那一处', () => {
    const controller = read('../desktop-controller.tsx')
    const statusbar = read('../shell/hooks/use-statusbar-items.tsx')

    expect(controller, '右栏那条时间轴又放了一颗打开看板的按钮').not.toMatch(/navigate\(\s*AGENDA_ROUTE\s*\)/)
    expect(statusbar, '状态栏那个带 pending 计数的入口没了').toMatch(/to:\s*AGENDA_ROUTE/)
  })
})
