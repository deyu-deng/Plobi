import { describe, expect, it } from 'vitest'

import { stripInternalDirectives } from './visible-user-text'

// Shape produced by plugins/plobi-north-star/hard_route.py (WP-BE-13).
const DIRECTIVE =
  '明天的日常安排是什么\n\n[§8.2 硬路由 · 本回合强制] 上面这句话命中总秘书冻结话术，必须严格按序执行：\n1. 第一动作只能是调用工具 plobi_secretary_ask'

describe('stripInternalDirectives (WP-UI-NO-INTERNALS)', () => {
  it('keeps only the pre-rewrite sentence of a hard-routed turn', () => {
    expect(stripInternalDirectives(DIRECTIVE)).toBe('明天的日常安排是什么')
  })

  it('is a pass-through for anything the hook did not touch', () => {
    expect(stripInternalDirectives('明天几点开会？')).toBe('明天几点开会？')
    expect(stripInternalDirectives('')).toBe('')
  })

  it('does not touch text that merely mentions the phrase', () => {
    const text = '看看这段：§8.2 硬路由 是后端的事'

    expect(stripInternalDirectives(text)).toBe(text)
  })
})
