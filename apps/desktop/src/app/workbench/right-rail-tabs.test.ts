/**
 * 档案/笔记的新形态（用户 11-07 当面拍板的三件事）：
 *
 * ① 右栏那个 `progress`（资料）页签**删掉**；
 * ② 右栏那个页签现在装的是**笔记**（`notes`）；
 * ③ **档案**变成对话界面右上角的一枚矢量图标（点它出档案卡片）。
 *
 * 这三条是**形态契约**，不是实现细节：页签名一变，左栏/右栏/浮层三处就要跟着对。
 * 这里钉的是**代码里真实存在的形状**（从源码读出来），不是自己写一份常量再断言它。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

// `src/app` —— 下面那些相对路径都是相对它写的
const APP = join(__dirname, '..')
// `src` —— i18n 在这儿，不在 `src/app` 底下
const SRC = join(__dirname, '..', '..')

function read(relative: string): string {
  return readFileSync(join(APP, relative), 'utf8')
}

describe('右栏页签：资料页签没了，换成笔记', () => {
  it('RightRailTab 里没有 progress，有 notes', () => {
    const src = read('workbench/right-rail.tsx')
    const block = src.slice(
      src.indexOf('export type RightRailTab ='),
      src.indexOf('export type RightRailTabId')
    )
    expect(block).toContain("'notes'")
    expect(block).not.toContain("'progress'")
  })

  it('右栏页签的标签与说明在四语里都叫「笔记」这一族', () => {
    // 屏上不许出现机器词，也不许留一个改了键却没改文案的旧说法
    for (const locale of ['zh', 'en', 'zh-hant', 'ja']) {
      const src = readFileSync(join(SRC, 'i18n', `${locale}.ts`), 'utf8')
      expect(src).toMatch(/notes:\s*'[^']+'/)
    }
  })

  it('右栏那一格装的是笔记列表，不是档案进度', () => {
    const src = read('desktop-controller.tsx')
    expect(src).toMatch(/notes:\s*activeAgentId\s*\?\s*<AgentNotesList/)
    expect(src).not.toMatch(/^\s*progress:\s*activeAgentId/m)
  })
})

describe('档案入口：对话界面右上角那一枚图标', () => {
  it('对话界面挂了档案卡片，不是进度面板', () => {
    const src = read('chat/index.tsx')
    expect(src).toContain('AgentDossierCard')
  })

  it('档案入口认的是 Agent 作用域（$agentScope），不是会话 id', () => {
    const src = read('chat/index.tsx')
    // 档案跟着 Agent 走：Plobi 没有「会话」这个概念，一个 Agent 就是一张嘴，
    // 同一个 Agent 的多轮对话共用同一份档案 ⇒ 键必须是 Agent id。
    expect(src).toContain('$agentScope')
    expect(src).toMatch(/AgentDossierCard agentId=\{agentScope\}/)
  })

  it('L1（秘书）下不出那个入口 —— 秘书没有自己的档案', () => {
    const src = read('chat/index.tsx')
    expect(src).toMatch(/agentScope\s*!==\s*'l1'/)
  })

  it('档案卡片的钩子不在提前 return 之后（React 规则）', () => {
    const src = read('chat/right-rail/agent-dossier-card.tsx')
    const hookAt = src.indexOf('useQuery(')
    const earlyReturnAt = src.indexOf('if (!agentId)')
    expect(hookAt).toBeGreaterThan(-1)
    // 钩子必须排在提前 return 之前
    expect(hookAt).toBeLessThan(earlyReturnAt)
  })
})
