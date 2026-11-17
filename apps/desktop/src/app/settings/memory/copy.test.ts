/**
 * 「记忆与上下文」这一屏的文案门（`WP-MEMORY-LAYERS`）。
 *
 * 钉两件事：
 *
 * ① **四个组名四语都真有**，而且日文／繁中不是英文的回落——`defineLocale` 缺键时会
 *    悄悄回落英文，「有键」不等于「翻过」，所以还要比一次值。组名取不到就整张卡
 *    不给抬头（渲染器刻意如此，不把键名印上屏），所以这条同时守着「别退化成没有组名」。
 *
 * ② **这一屏每个字段标题，三个译文语种都有**。英文真源在 `FIELD_LABELS.memory`，
 *    缺一条界面上就直接露出英文。这一屏原本就烂在这里：14 个键里 9 个从来没翻过
 *    （三道写闸、五个上限、下沉压线、提醒间隔全裸着）。
 *
 * `Mind` 是产品名，三种译文里都叫 Mind，不许被译走。
 */
import { describe, expect, it } from 'vitest'

import { FIELD_LABELS } from '../constants'
import { en } from '@/i18n/en'
import { ja } from '@/i18n/ja'
import { zh } from '@/i18n/zh'
import { zhHant } from '@/i18n/zh-hant'

const TRANSLATED = [
  ['zh', zh],
  ['ja', ja],
  ['zh-hant', zhHant]
] as const

const GROUP_KEYS = ['working', 'longTerm', 'mind', 'external'] as const

/**
 * 字段标题两边都是**扁平的点路径**：英文在 `FIELD_LABELS['memory.writeApproval']`，
 * 译文在 `settings.fieldLabels['memory.writeApproval']`（同样由 `defineFieldCopy` 展开）。
 * 组名不一样——那是嵌套对象（`settings.groups.memory.working`）。
 */
const englishMemoryKeys = Object.keys(FIELD_LABELS).filter(key => key.startsWith('memory.'))

describe('记忆与上下文的文案', () => {
  it('四个组名四语都有，且 Mind 在三种译文里就叫 Mind', () => {
    expect(Object.keys(en.settings.groups.memory).sort()).toEqual([...GROUP_KEYS].sort())

    for (const [lang, copy] of TRANSLATED) {
      const groups = copy.settings.groups.memory

      for (const key of GROUP_KEYS) {
        expect(groups[key], `${lang} 缺 groups.memory.${key}`).toBeTruthy()
      }

      expect(groups.mind, `${lang} 把 Mind 译走了`).toBe('Mind')
    }
  })

  it('英文组名不是其它语种的回落（彼此相同就说明没翻）', () => {
    for (const [lang, copy] of TRANSLATED) {
      const groups = copy.settings.groups.memory

      for (const key of ['working', 'longTerm', 'external'] as const) {
        expect(groups[key], `${lang} 的 ${key} 与英文一模一样，是回落`).not.toBe(en.settings.groups.memory[key])
      }
    }
  })

  it('这一屏每个字段标题，三个译文语种都有', () => {
    // 三条写闸 ＋ 五个上限 ＋ 压线 ＋ 提醒 ＋ 画像/记忆开关 ＋ 服务 —— 少一条就是少翻。
    expect(englishMemoryKeys.length).toBeGreaterThanOrEqual(13)

    for (const [lang, copy] of TRANSLATED) {
      const block = copy.settings.fieldLabels

      for (const key of englishMemoryKeys) {
        expect(block?.[key], `${lang} 缺 fieldLabels['${key}']（屏上会露英文）`).toBeTruthy()
      }
    }
  })
})
