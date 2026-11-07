import { describe, expect, it } from 'vitest'

import { en } from './en'
import { ja } from './ja'
import { zh } from './zh'
import { zhHant } from './zh-hant'

/**
 * 产品文案语域门。
 *
 * 管的是「用户看得见的静态文案」：i18n 四语目录里的全部字符串。
 * 不管「用户与 Agent 的对话」——那是运行时可变内容，不在本门范围。
 *
 * 判断标准只收可判定的项：命中就给路径、原文、改法。
 * 长度、破折号、分号一类不做硬判据——各语言排版惯例不同，硬设阈值会
 * 在第一次提交时就被 `--no-verify` 绕过，等于没做。
 */

interface Rule {
  id: string
  label: string
  pattern: RegExp
  advice: string
}

const ZH_RULES: Rule[] = [
  {
    id: 'C1',
    label: '句末语气词',
    pattern: /[啦呀呢吧哈哦哟嘛][。！!？?]?$/,
    advice: '去掉句末语气词，改用陈述句收尾',
  },
  {
    id: 'C2',
    label: '第一人称代办腔',
    pattern: /我(来|來|先|这就|這就)?(帮|幫)|(让我|讓我)(来|來|先)?|以下是为您|以下為您|我为你|我為您|我给你(?!的)|我給您/,
    advice: '产品不替用户动手，直接写动作本身，如「打开设置」而非「我来帮你打开设置」',
  },
  {
    id: 'C3',
    label: '空形容词',
    pattern: /丝滑|絲滑|无缝|無縫|极致|極致|强大|強大|高效|一站式|一键|一鍵|轻松搞定|輕鬆搞定|省心|完美|震撼/,
    advice: '删掉形容词，写清它到底做什么',
  },
  {
    id: 'C4',
    label: '口语词',
    pattern: /搞定|弄好|点一下|點一下|点开(?!关)|點開(?!關)|东西|東西|稍微|挺/,
    advice: '换成书面动词：完成、打开、内容、略',
  },
  {
    id: 'C5',
    label: '第一人称复数',
    pattern: /我们|我們|咱们|咱們/,
    advice: '不用「我们」代言用户，改成不带主语或直呼动作',
  },
]

const EN_RULES: Rule[] = [
  {
    id: 'E1',
    label: '俚语营销词',
    pattern: /\b(awesome|amazing|seamless|effortless|oops|gonna|wanna|kinda|yay|super easy)\b/i,
    advice: '换成具体行为描述，不用评价性形容词',
  },
  {
    id: 'E2',
    label: '第一人称',
    pattern: /\b(we|our|us)\b/i,
    advice: '去掉第一人称，用被动或直接指称对象',
  },
  {
    id: 'E4',
    label: '寒暄与安抚',
    pattern: /\b(no worries|don't worry|feel free|sorry)\b/i,
    advice: '直接说发生了什么、能做什么，不安抚',
  },
]

const JA_RULES: Rule[] = [
  {
    id: 'J1',
    label: '口语终助词',
    pattern: /(だよ|だね|かな|じゃん|だぜ)[。！!]?$/,
    advice: '改用です・ます体收尾',
  },
  {
    id: 'J2',
    label: '第一人称',
    pattern: /(私たち|僕|俺)/,
    advice: '不用第一人称，直接描述对象与动作',
  },
]

/**
 * 按 key 精确豁免。每条都必须写清为什么——没有理由的豁免就是在开后门。
 */
const ALLOWED: Record<string, string[]> = {
  // 占位示例留空：存量已清零，新增豁免需附理由。
}

function stripPlaceholders(value: string): string {
  return value.replace(/\$\{[^}]*\}/g, '')
}

/** 递归取出 catalog 里所有用户可见字符串，含模板函数里的字面量。 */
function strings(node: unknown, path = ''): Array<[string, string]> {
  if (typeof node === 'string') {
    return [[path, node]]
  }
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

interface Violation {
  rule: Rule
  path: string
  value: string
}

function collect(catalog: unknown, rules: Rule[]): Violation[] {
  const found: Violation[] = []
  for (const [path, value] of strings(catalog)) {
    const plain = stripPlaceholders(value)
    for (const rule of rules) {
      if (!rule.pattern.test(plain)) {
        continue
      }
      const exempt = ALLOWED[path]
      if (exempt && exempt.includes(rule.id)) {
        continue
      }
      found.push({ rule, path, value })
    }
  }
  return found
}

function format(violations: Violation[]): string {
  return violations
    .map(v => `  [${v.rule.id} ${v.rule.label}] ${v.path}\n    原文：${v.value}\n    改法：${v.rule.advice}`)
    .join('\n')
}

describe('产品文案语域门', () => {
  it('简中', () => {
    const violations = collect(zh, ZH_RULES)
    expect(violations, `\n${format(violations)}\n`).toHaveLength(0)
  })

  it('繁中', () => {
    const violations = collect(zhHant, ZH_RULES)
    expect(violations, `\n${format(violations)}\n`).toHaveLength(0)
  })

  it('英文', () => {
    const violations = collect(en, EN_RULES)
    expect(violations, `\n${format(violations)}\n`).toHaveLength(0)
  })

  it('日文', () => {
    const violations = collect(ja, JA_RULES)
    expect(violations, `\n${format(violations)}\n`).toHaveLength(0)
  })
})

/**
 * 判据自检：每条规则必须能抓到已知反例。
 * 否则正则写歪了门会永远绿——那比没有门更坏。
 */
describe('判据自检', () => {
  const NEGATIVE_ZH = [
    '同步完成啦',
    '我来帮你打开设置',
    '丝滑的写作体验',
    '一键搞定部署',
    '我们要构建什么？',
  ]
  const NEGATIVE_EN = ['Awesome result', "We couldn't reach the server", 'No worries, try again']
  const NEGATIVE_JA = ['これは大事だよ', '私たちが処理します']

  it('中文规则全部有牙', () => {
    for (const rule of ZH_RULES) {
      expect(
        NEGATIVE_ZH.some(sample => rule.pattern.test(sample)),
        `规则 ${rule.id}（${rule.label}）抓不到任何反例，判据失效`
      ).toBe(true)
    }
  })

  it('英文规则全部有牙', () => {
    for (const rule of EN_RULES) {
      expect(
        NEGATIVE_EN.some(sample => rule.pattern.test(sample)),
        `规则 ${rule.id}（${rule.label}）抓不到任何反例，判据失效`
      ).toBe(true)
    }
  })

  it('日文规则全部有牙', () => {
    for (const rule of JA_RULES) {
      expect(
        NEGATIVE_JA.some(sample => rule.pattern.test(sample)),
        `规则 ${rule.id}（${rule.label}）抓不到任何反例，判据失效`
      ).toBe(true)
    }
  })

  it('合格文案不被误伤', () => {
    const good = [
      '打开设置',
      '上次连接显示未登录，需要重新登录',
      '会话已归档，可从归档恢复',
      'Open settings',
      'The update server could not be reached.',
      '設定を開く',
    ]
    for (const sample of good) {
      const matched = [...ZH_RULES, ...EN_RULES, ...JA_RULES].filter(r => r.pattern.test(sample))
      expect(matched.map(r => r.id), `合格文案「${sample}」被误伤`).toHaveLength(0)
    }
  })
})
