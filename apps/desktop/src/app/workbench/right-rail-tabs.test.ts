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
import { existsSync, readFileSync } from 'node:fs'
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

  it('档案卡默认并排在对话右侧的一列（Qoder 实证：默认 fix ＝ 固定在任务右侧，对话从不被遮挡）', () => {
    // 用户 11-09 11:47：「在对话区单独展开的时候不是说了档案要默认显示吗？**而且不要
    // 遮挡对话区**」⇒ 默认形态必须是**并排占位**，不是 absolute 浮层。
    // Qoder 的实证（11-09 11:5x 从 asar 取）：`taskMonitorMode` 默认 `"fix"`
    // （「固定在任务右侧」），会话内监控面板本体是 280px 的 **flex 兄弟列**
    // （区域 `CHAT_SESSION_B1_2_REGION`），不是 absolute 浮层；`"floating"` 是用户
    // 可配的另一种偏好。默认做成浮层 ⇒ 档案卡一露脸就盖住对话消息（现场实况）。
    const topBar = read('shell/chat-top-bar.tsx')
    expect(topBar).not.toContain('Popover')

    const controller = read('desktop-controller.tsx')
    expect(controller).toContain('<DossierPanel')

    // 默认**并排**：`$dossierPanelPinned` 初值是 true。
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')
    expect(layout).toMatch(/\$dossierPanelPinned\s*=\s*atom\(true\)/)

    // 钉住那一套：对话右侧的一列（带左边线，不带圆角）；对话容器在钉住时是并排。
    expect(controller).toMatch(/DOSSIER_PINNED\s*=\s*'[^']*border-l/)
    expect(controller).toMatch(/dossierVisible && dossierPinned \? 'flex-row' : 'flex-col'/)

    // 浮层是**用户切出来**的另一种形态：绝对定位 ＋ 圆角 ＋ 投影。
    expect(controller).toMatch(/DOSSIER_FLOATING\s*=\s*\n\s*'absolute[^']*rounded-xl/)
    // 浮层压在**抬头下面**：抬头右边那几枚（档案图标本身、全屏、收起）都住在那一行，
    // 压上去就点不到了（11-09：档案卡一露脸就把自己的开关盖住了）。
    // 减号两边不留空格的 calc 是**非法 CSS** ⇒ 浏览器整条丢掉（这一档就白写了）。
    const floating = controller.match(/DOSSIER_FLOATING\s*=\s*\n\s*'([^']*)'/)?.[1] ?? ''
    expect(floating).toContain('card-header-height')
    expect(floating).not.toMatch(/calc\(100%-[0-9]/)
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

/**
 * WP-TWO-CARDS（用户 11-09 11:47）：界面上**只有两张卡**——对话区卡片 ＋ 工作区卡片。
 *
 * ① 预览轨（对话旁那第三块）的**实现删掉**，不是藏起来；
 * ② 笔记/文件预览落在**工作区卡片的页签**里；
 * ③ 对话区单独展开时档案卡默认显示且**不遮挡对话**。
 *
 * 这一组钉的是**文件与代码的真实形状**：文件在不在、源码里还提不提那条轨。
 */
describe('只有两张卡：预览轨的实现删掉了，笔记落工作区卡片的页签', () => {
  it('预览轨那几个实现文件已经不在了（删，不是藏）', () => {
    const rail = join(APP, 'chat', 'right-rail')

    // 轨的本体、它的面板、它的控制台，以及那个 barrel。
    expect(existsSync(join(rail, 'index.tsx'))).toBe(false)
    expect(existsSync(join(rail, 'index.ts'))).toBe(false)
    expect(existsSync(join(rail, 'preview.tsx'))).toBe(false)
    expect(existsSync(join(rail, 'preview-pane.tsx'))).toBe(false)
    expect(existsSync(join(rail, 'preview-console.tsx'))).toBe(false)
  })

  it('预览轨那套管线在 store 里也没了（窗格 id ＋ 右栏页签态 ＋ 右栏视图）', () => {
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')

    expect(layout).not.toContain('PREVIEW_PANE_ID')
    expect(layout).not.toContain('RIGHT_RAIL_PREVIEW_TAB_ID')
    expect(layout).not.toContain('selectRightRailTab')
    expect(layout).not.toContain('$rightRailView')
    expect(layout).not.toContain('$rightRailActiveTabId')
  })

  it('壳上不再挂那条轨（对话卡旁边的第三块）', () => {
    const controller = read('desktop-controller.tsx')

    expect(controller).not.toContain('ChatPreviewRail')
    expect(controller).not.toContain('PREVIEW_RAIL_')
    expect(controller).not.toContain('PREVIEW_PANE_ID')
    // 壳上那个「预览轨开着没有」的 prop 也一起没了。
    expect(read('shell/app-shell.tsx')).not.toContain('previewPaneOpen')
  })

  it('文件预览的组件搬到工作区那边去了（工作区卡片的页签要靠它渲染）', () => {
    // 预览轨删了，但它那份「渲染一个文件」的能力不能跟着没——工作区卡片要拿它画页签。
    const moved = join(SRC, 'app', 'workbench', 'file-preview.tsx')

    expect(existsSync(moved)).toBe(true)
    expect(readFileSync(moved, 'utf8')).toContain('export function LocalFilePreview')
    expect(existsSync(join(APP, 'chat', 'right-rail', 'preview-file.tsx'))).toBe(false)
  })

  it('点笔记走「在工作区卡片里开一个页签」这条通道，不再走预览轨', () => {
    const notes = read('chat/right-rail/notes-list.tsx')

    expect(notes).toContain('requestWorkbenchFileOpen')
    // 判的是**代码**，不是注释（上面那段注释本来就写着「它以前走的是哪条路」）。
    expect(notes).not.toMatch(/import[^;]*setRightRailView/)
    expect(notes).not.toMatch(/import[^;]*setCurrentSessionPreviewTarget/)

    // 通道本身：调用方只管「我要开这个」，落页签由 desktop-controller 做。
    const channel = readFileSync(join(SRC, 'store', 'workbench-tabs.ts'), 'utf8')

    expect(channel).toContain('export function requestWorkbenchFileOpen')
    expect(channel).toContain('$workbenchFileOpenRequest')

    // 落页签那一侧在 desktop-controller 里，且认得出当前是 L1 还是 L2 那条腿。
    // 它订阅的是那条通道的原子（`$workbenchFileOpenRequest`），所以这里认原子名，
    // 不认调用方的函数名。
    expect(read('desktop-controller.tsx')).toContain('$workbenchFileOpenRequest')
  })

  it('对话区单独展开时，档案卡默认在，而且不压住对话（并排占位）', () => {
    const controller = read('desktop-controller.tsx')

    // 显隐：`auto` 那一档跟着对话卡全屏走 ⇒ 对话区单独展开时档案卡**默认在**。
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')

    expect(layout).toMatch(/focus === 'chat'/)
    // 形态：默认并排（对话右侧占一列）⇒ 对话从不被它盖住。
    expect(layout).toMatch(/\$dossierPanelPinned\s*=\s*atom\(true\)/)
    expect(controller).toMatch(/DOSSIER_PINNED\s*=\s*'[^']*border-l/)
    // 对话容器在钉住时是并排（`flex-row`），浮层那档才用 absolute。
    expect(controller).toMatch(/dossierVisible && dossierPinned \? 'flex-row' : 'flex-col'/)
  })
})
