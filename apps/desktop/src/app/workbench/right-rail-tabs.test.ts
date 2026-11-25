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

import { DEFAULT_SLOT_DECLARATION } from '../shell/slot-registry'

// `src/app` —— 下面那些相对路径都是相对它写的
const APP = join(__dirname, '..')
// `src` —— i18n 在这儿，不在 `src/app` 底下
const SRC = join(__dirname, '..', '..')

function read(relative: string): string {
  return readFileSync(join(APP, relative), 'utf8')
}

/**
 * 对话卡右上角那一族的**唯一容器**（11-19 16:0x 统一容器那一刀）。
 * 列宽、浮层那层皮、高度上限都在它那儿 ⇒ 这几件事的判据读它，不读控制器。
 */
function monitorPanel(): string {
  return read('shell/monitor-panel.tsx')
}

describe('右栏页签：资料页签没了，换成笔记', () => {
  it('页签清单里没有 progress，有 notes', () => {
    // 11-25 更新（裁定 239）：页签身份放开了（`RightRailTabId` 是开放的——类型写死
    // 正是"插件加不进页签"的病根），**清单的真源搬到了 slot 声明表** ⇒ 这一条改查声明表。
    const ids = DEFAULT_SLOT_DECLARATION.filter(
      slot => slot.position === 'workspace' && slot.region === 'tabs'
    ).map(slot => slot.id)

    expect(ids).toContain('notes')
    expect(ids).not.toContain('progress')
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
    // 11-19 17:0x：三枚入口收成**同一个组件**（`shell/monitor-entry-button.tsx`），
    // 抬头只管挂那三枚，行为与形状都在那一个件里。
    expect(topBar).toContain('DossierButton')
    expect(read('shell/monitor-entry-button.tsx')).toContain('toggleMonitorFace')
    // 旧壳里不留第二份入口——两份形状迟早会漂。
    expect(read('chat/index.tsx')).not.toContain('toggleMonitorFace')
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
    expect(controller).toContain('<MonitorRegion')

    // 默认**并排**：`$monitorPinned` 初值是 true。
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')
    expect(layout).toMatch(/\$monitorPinned\s*=\s*atom\(true\)/)

    // 钉住那一套：对话右侧的一列（固定窄宽、不收缩）；对话容器在钉住时是并排。
    // 11-18 按 Qoder 实况去掉了这条左边线，列靠背景分区 ⇒ 这里认「固定宽度的一列」，
    // 不再认 border，否则测试会和实现对着干。
    // 11-19 16:0x 统一容器那一刀：**列宽不再由控制器写**（那把尺归
    // `shell/monitor-panel.tsx` 那个件管，Qoder `w-[280px]`），控制器只给这一列
    // 「不收缩 ＋ 别把对话挤没」两件事。
    expect(controller).toMatch(/DOSSIER_PINNED\s*=\s*'[^']*shrink-0'/)
    expect(monitorPanel()).toMatch(/MONITOR_COLUMN_SHAPE\s*=\s*'[^']*w-\[280px\]/)
    expect(controller).toMatch(/monitorVisible && monitorPinned[^?]*\? 'flex-row' : 'flex-col'/)

    // 浮层是**用户切出来**的另一种形态：抬起来的一层（圆角 ＋ 淡边框 ＋ 投影）。
    // 11-19 16:0x 起这层皮归统一容器那个件（`MONITOR_FLOAT_SHAPE`）。
    const floatBlock = monitorPanel().slice(
      monitorPanel().indexOf('export const MONITOR_FLOAT_SHAPE'),
      monitorPanel().indexOf('export const MONITOR_COLUMN_SHAPE')
    )

    expect(floatBlock).toContain('rounded-lg')
    expect(floatBlock).toContain('shadow-lg')
    expect(floatBlock).toContain('w-[280px]')
    expect(controller).not.toContain('DOSSIER_FLOATING_SHAPE')
    // 11-19 22:0x：浮层**挂到 body**（portal ＋ `fixed` 壳）—— 原来挂在对话卡里
    // （`absolute`），祖先的 `overflow-hidden` 会把它裁掉 ⇒ 拖到工作区卡上就看不见
    // （用户原话：「拖到工作区卡片的时候，会藏到卡片底下导致看不到」）。
    // 位置因此改成**视口坐标**：控制器只给「锚」与「拖出来的位置」，不再自己定位。
    const region = read('shell/monitor-region.tsx')
    expect(region).toContain('createPortal')
    expect(region).toContain('data-monitor-float-layer')
    expect(region).toContain('fixed inset-0 z-[1000]')
    expect(controller).toContain('anchorElement={chatCardElement}')
    expect(controller).toContain('floatPosition={monitorFloatPosition}')
    // 浮层压在**抬头下面**：抬头右边那几枚（档案图标本身、全屏、收起）都住在那一行，
    // 压上去就点不到了（11-09：档案卡一露脸就把自己的开关盖住了）。
    // 11-19 22:0x 起这一条不写在类名里，写成**从顶栏那一行的下沿算**（同一个数）。
    expect(region).toContain('[data-chat-topbar]')
    // 高度上限是**一个数**（Qoder `max-h-[560px]`）：不许再写「按百分比算」那种
    // 减号两边留空格的非法 calc。
    expect(floatBlock).toContain('max-h-[560px]')
    expect(floatBlock).not.toMatch(/calc\(100%-[0-9]/)
    // 对话容器仍然是 relative（拖放落点在那儿）。
    expect(controller).toContain("'relative flex h-full min-h-0'")
  })

  it('图钉那一档：浮层与固定由 store 里的一枚图钉切', () => {
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')
    expect(layout).toContain('$monitorPinned')
    expect(layout).toContain('toggleMonitorPin')
  })

  it('显隐口径：对话卡全屏时默认显示，工作区在显示时默认收起，点图标才展开', () => {
    const layout = readFileSync(join(SRC, 'store', 'layout.ts'), 'utf8')
    expect(layout).toContain('$monitorSlotVisible')
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
    // 后端把它翻译成名册里 `role=conductor` 那一行（名册是唯一真源）。
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
    expect(layout).toMatch(/\$monitorPinned\s*=\s*atom\(true\)/)
    // 列宽归统一容器那个件（Qoder `w-[280px]`），控制器只给「不收缩」。
    expect(controller).toMatch(/DOSSIER_PINNED\s*=\s*'[^']*shrink-0'/)
    expect(monitorPanel()).toMatch(/MONITOR_COLUMN_SHAPE\s*=\s*'[^']*w-\[280px\]/)
    // 对话容器在钉住时是并排（`flex-row`），浮层那档才用 absolute。
    expect(controller).toMatch(/monitorVisible && monitorPinned[^?]*\? 'flex-row' : 'flex-col'/)
  })
})
