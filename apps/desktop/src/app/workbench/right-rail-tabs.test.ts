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
  it('入口挂在**真正在用的那一面**的抬头上', () => {
    // console 这一面用的是 `ChatSurface`，它上面那张抬头是 `ChatTopBar`
    // （`desktop-views.tsx` 里紧挨着渲染）。旧的 `chat/index.tsx` 那一套壳已经不在
    // 路上了（11-05 撤了纯对话路由），入口挂在那儿就一次都没在用户眼前出现过
    // （用户 11-08 16:4x：「对了档案的图标怎么还没有出现呀」）。
    const topBar = read('shell/chat-top-bar.tsx')
    expect(topBar).toContain('toggleDossierPanel')
    // 旧壳里不留第二份入口——两份形状迟早会漂。
    expect(read('chat/index.tsx')).not.toContain('toggleDossierPanel')
  })

  it('档案卡是浮在对话上的那张卡（Qoder 实物截图：Floating ＋ 图钉，不是 Popover 小弹窗）', () => {
    // 用户 11-08 22:3x 给了 Qoder「任务监控」的**实物截图**：那张卡默认**浮在对话
    // 右上角**（圆角 ＋ 投影，对话不被挤窄），抬头右上角一枚**图钉**把它钉成对话
    // 右侧的一列。之前那版照 asar 词条做成了停靠列——形状不对。
    const topBar = read('shell/chat-top-bar.tsx')
    expect(topBar).not.toContain('Popover')

    const controller = read('desktop-controller.tsx')
    expect(controller).toContain('<DossierPanel')
    // 浮层那一套尺寸：绝对定位 ＋ 圆角 ＋ 投影。
    expect(controller).toMatch(/DOSSIER_FLOATING\s*=\s*\n\s*'absolute[^']*rounded-xl/)
    // 浮层压在**抬头下面**：抬头右边那几枚（档案图标本身、全屏、收起）都住在那一行，
    // 压上去就点不到了（11-09：档案卡一露脸就把自己的开关盖住了）。
    // 减号两边不留空格的 calc 是**非法 CSS** ⇒ 浏览器整条丢掉（这一档就白写了）。
    const floating = controller.match(/DOSSIER_FLOATING\s*=\s*\n\s*'([^']*)'/)?.[1] ?? ''
    expect(floating).toContain('card-header-height')
    expect(floating).not.toMatch(/calc\(100%-[0-9]/)
    // 钉住那一套：对话右侧的一列（带左边线，不带圆角）。
    expect(controller).toMatch(/DOSSIER_PINNED\s*=\s*'[^']*border-l/)
    // 对话容器要 relative，浮层才贴得住它的右上角。
    expect(controller).toContain("'relative flex h-full min-h-0'")
  })

  it('图钉那一档：浮层与固定由 store 里的一枚图钉切', () => {
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')
    expect(layout).toContain('$dossierPanelPinned')
    expect(layout).toContain('toggleDossierPanelPin')
  })

  it('显隐口径：对话卡全屏时默认显示，工作区在显示时默认收起，点图标才展开', () => {
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')
    expect(layout).toContain('$dossierPanelVisible')
    // `auto` 那一档跟着对话卡全屏与否走（Qoder 的默认形态）。
    expect(layout).toMatch(/focus === 'chat'/)
    // 用户点过之后才固定（open / closed 两档）。
    expect(layout).toMatch(/mode === 'open'/)
    expect(layout).toMatch(/mode === 'closed'/)
  })

  it('档案跟着 Agent 走，不跟着会话走', () => {
    const src = read('desktop-controller.tsx')
    // Plobi 没有「会话」这个概念，一个 Agent 就是一张嘴，一个 Agent 的多轮对话
    // 共用同一份档案 ⇒ 键必须是 Agent id。L1 那一格给哨兵 `'l1'`（总秘书），
    // 后端把它翻译成名册里 `role=l1_secretary` 那一行（名册是唯一真源）。
    expect(src).toMatch(/chatScope\??\.kind === 'agent' \? chatScope\.id : 'l1'/)
  })

  it('入口在每个作用域都出现 —— 不许按作用域把它藏起来', () => {
    const src = read('shell/chat-top-bar.tsx')
    // L1 那一屏也是主界面本体，它同样有一格档案；秘书没有档案 ≠ 秘书这一屏
    // 不许有入口。
    expect(src).not.toMatch(/scope\.kind === 'agent' &&/)
    expect(src).not.toMatch(/scope\.kind !== 'l1'/)
  })

  it('档案卡的钩子不在提前 return 之后（React 规则）', () => {
    const src = read('dossier/dossier-panel.tsx')
    const hookAt = src.indexOf('useQuery(')
    const earlyReturnAt = src.indexOf('if (!agentId)')
    expect(hookAt).toBeGreaterThan(-1)
    // 钩子必须排在提前 return 之前
    expect(hookAt).toBeLessThan(earlyReturnAt)
  })
})
