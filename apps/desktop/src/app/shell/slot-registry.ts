/**
 * 界面外壳的挂载点（slot）—— 裁定 239 / 规格 `Docs/specs/shell-slots.md`。
 *
 * **slot ＝ 宿主分配好位置与形状契约的一个挂载点；宿主管形状，插件填内容。**
 *
 * 它**不是"一个能塞任何东西的黑洞"**：每个 slot 说清——
 * id／属于哪个位置／那一块区域／哪种形状家族／位置与顺序／插件能往里放什么（props 契约）／
 * 默认挂什么。
 *
 * ## 三个位置 ＋ 区域 ＋ 形状家族
 *
 * 宿主留下的界面只有三个位置：**侧边栏、一张对话卡片、工作区**。
 *
 * 一个位置里可以有好**块区域**，块的**形状家族**决定宿主管多少：
 *
 * | 位置 | 区域 | 家族 | 说明 |
 * |---|---|---|---|
 * | sidebar | `top` | `entry-row` | 上面那排导航格子（图标＋文字，钉在顶上不滚动） |
 * | sidebar | `middle` | `panel` | 中间那一大块：**名单 ↔ 外包** 两页来回切（会滚动） |
 * | sidebar | `bottom` | `entry-row` | 下面那排状态栏格子（**与上面同一族**——代码里本来就写着「底部这两枚…和上面那排图标同一个形状」） |
 * | chat-card | `top-right` | `entry-row` | 对话卡右上角那三枚（档案／待放行／写脑待批） |
 * | workspace | `tabs` | `entry-row` | 工作区的页签条 |
 *
 * ⚠️ **同一个短名可能在两块区域里各出现一次**（`agenda`：左栏下面那枚 ＋ 工作区那个
 * 页签）⇒ **slot 的身份是「位置＋区域＋短名」**，不是短名本身。
 *
 * **出厂那一份＝默认插件**（用户 11-25 12:0x 拍定）：它与用户装的插件走同一条
 * 注册表、同一个 slot、同一套开关——**用户可以把它关掉**。
 */

import { ARTIFACTS_ROUTE, DIARY_ROUTE, KNOWLEDGE_ROUTE, MESSAGING_ROUTE, SKILLS_ROUTE } from '../routes'

/** 宿主留下的三个位置。 */
export type SlotPosition = 'sidebar' | 'chat-card' | 'workspace'

/** 宿主声明过的位置（插件自己声明 slot 时，位置只能从这里挑）。 */
const POSITIONS: readonly SlotPosition[] = ['sidebar', 'chat-card', 'workspace']

/**
 * 形状家族——**同一族的挂载点，宿主管的规矩一样**。
 *
 * `entry-row`＝一排格子（侧边栏上面那排、下面那排、对话卡那三枚、工作区页签条）；
 * `panel`＝一整块面板（侧边栏中间那一大块）；
 * `action`＝**一个独立的行动按钮**——它不是导航行里的一格，**用不同的组件渲染**
 * （DSH 那个「New Session」：透明底＋内描边的一枚整行按钮，与那些图标行一眼分得开）。
 */
export type SlotKind = 'action' | 'entry-row' | 'panel'

/** 一格的形状约束——**宿主定，插件不能越界**。 */
export interface SlotShape {
  /** 画不画图标。 */
  icon: boolean
  /** 写不写标题。 */
  label: boolean
}

/**
 * 一格里的东西。
 *
 * 全是**数据**——没有组件、没有函数：浅的那一步（声明式 slot）不许插件在界面上
 * 执行代码（那要打包链＋沙箱＋权限模型，是"深"的那一步）。
 */
export interface SlotContent {
  /** 屏上/路由那一侧用的短名（出厂那几格就是它自己的名字）。 */
  id: string
  /** 图标名（codicon）。 */
  icon?: string
  /** 屏上写的名字——插件来的格子用这个（出厂那几格走 i18n 的键）。 */
  label?: string
  /** i18n 的键（出厂那几格的标题走它）。 */
  labelKey?: string
  /** 点开去哪儿。 */
  route?: string
  /**
   * 这一格显示应用的哪一页（`AppView` 的 id）。
   *
   * **插件的页签靠它拿到真内容**：浅的那一步插件不能在界面上执行代码，所以那一格
   * 由**宿主**按这个 view 找到应用里的那一页并渲染（不是一个占位）——见 `app/views.ts`。
   */
  view?: string
  /** 谁提供的（出厂那一份是 `builtin`）。 */
  providedBy?: string
}

/** 插件填进来的一格：填哪个 slot ＋ 填什么。 */
export interface SlotContribution extends SlotContent {
  /** 填哪个 slot（`位置/区域/短名`，如 `workspace/tabs/my-tab`）。 */
  slotId: string
  /**
   * 插件**自己声明**一个新 slot 时给的那几样（位置／区域／顺序／形状）。
   *
   * 没有它们时，这一格只能**填**宿主声明过的 slot；有了它们，插件就能**新增**一个
   * slot（比如工作区多一个页签）——但位置仍得是宿主声明过的那几个。
   */
  position?: SlotPosition
  region?: string
  kind?: SlotKind
  order?: number
  shape?: SlotShape
  /** 在哪些壳里出现（`l1`／`l2`）；不给就是两个壳都有。 */
  shells?: string[]
}

/** 一个挂载点的声明。 */
export interface SlotDeclaration {
  /** 稳定标识（在「位置＋区域」内唯一）。 */
  id: string
  /** 属于哪个位置。 */
  position: SlotPosition
  /** 那一块区域（一个位置里可以有几块：侧边栏＝上面／中间／下面）。 */
  region: string
  /** 哪种形状家族。 */
  kind: SlotKind
  /** 在那块区域里的顺序。 */
  order: number
  /** 形状约束。 */
  shape: SlotShape
  /** 插件能往里放什么的契约。 */
  props: Record<string, unknown>
  /** 出厂默认挂什么（默认插件——用户可移除）。 */
  default?: SlotContent
}

/**
 * 出厂那一份声明（**用户可以整份替换**——裁定 238 补记 1 的"整份 override"在界面
 * 上的落点）。
 *
 * 这三段就是今天写死在那三处的清单：侧边栏五项、工作区六型、对话卡那三枚，
 * 外加左栏下面那排状态栏格子。它们**不是产品常量，是出厂预装的那一批插件**
 * （用户 11-25 12:0x：「内置的表也算插件」）。
 */
export const DEFAULT_SLOT_DECLARATION: readonly SlotDeclaration[] = [
  // ── 侧边栏 · 上面那排（与下面那排同一族：一排格子）────────────────────────
  {
    id: 'skills',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 10,
    shape: { icon: true, label: true },
    props: { opens: 'capabilities' },
    default: {
      id: 'skills',
      icon: 'symbol-misc',
      labelKey: 'sidebar.nav.skills',
      providedBy: 'builtin',
      route: SKILLS_ROUTE
    }
  },
  {
    id: 'messaging',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 20,
    shape: { icon: true, label: true },
    props: { opens: 'messaging' },
    default: {
      id: 'messaging',
      icon: 'comment',
      labelKey: 'sidebar.nav.messaging',
      providedBy: 'builtin',
      route: MESSAGING_ROUTE
    }
  },
  {
    id: 'artifacts',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 30,
    shape: { icon: true, label: true },
    props: { opens: 'artifacts' },
    default: {
      id: 'artifacts',
      icon: 'files',
      labelKey: 'sidebar.nav.artifacts',
      providedBy: 'builtin',
      route: ARTIFACTS_ROUTE
    }
  },
  {
    id: 'knowledge',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 40,
    shape: { icon: true, label: true },
    props: { opens: 'knowledge' },
    default: {
      id: 'knowledge',
      icon: 'book',
      labelKey: 'sidebar.nav.knowledge',
      providedBy: 'builtin',
      route: KNOWLEDGE_ROUTE
    }
  },
  {
    id: 'diary',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 50,
    shape: { icon: true, label: true },
    props: { opens: 'diary' },
    default: {
      id: 'diary',
      icon: 'edit',
      labelKey: 'sidebar.nav.diary',
      providedBy: 'builtin',
      route: DIARY_ROUTE
    }
  },

  {
    id: 'new-agent',
    position: 'sidebar',
    region: 'top',
    kind: 'action',
    order: 0,
    shape: { icon: true, label: true },
    props: { action: 'new-agent' },
    default: { id: 'new-agent', icon: 'add', labelKey: 'sidebar.newAgentLabel', providedBy: 'builtin' }
  },

  // ── 侧边栏 · 中间那一大块（另一族：一整块面板；名单 ↔ 外包 两页）────────────
  {
    id: 'roster',
    position: 'sidebar',
    region: 'middle',
    kind: 'panel',
    order: 10,
    shape: { icon: true, label: true },
    props: { opens: 'agent-roster', tab: 'agents' },
    default: { id: 'roster', icon: 'organization', providedBy: 'builtin' }
  },
  {
    id: 'outsourced',
    position: 'sidebar',
    region: 'middle',
    kind: 'panel',
    order: 20,
    shape: { icon: true, label: true },
    props: { opens: 'agent-roster', tab: 'outsourced' },
    default: { id: 'outsourced', icon: 'send', providedBy: 'builtin' }
  },

  // ── 侧边栏 · 下面那排（与上面同一族；内容由状态栏那条管道给）──────────────
  {
    id: 'system:settings',
    position: 'sidebar',
    region: 'bottom',
    kind: 'entry-row',
    order: 10,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar' },
    default: { id: 'system:settings', icon: 'gear', providedBy: 'builtin' }
  },
  {
    id: 'agents',
    position: 'sidebar',
    region: 'bottom',
    kind: 'entry-row',
    order: 20,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar' },
    default: { id: 'agents', icon: 'organization', providedBy: 'builtin' }
  },
  {
    id: 'cron',
    position: 'sidebar',
    region: 'bottom',
    kind: 'entry-row',
    order: 30,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar' },
    default: { id: 'cron', icon: 'clock', providedBy: 'builtin' }
  },
  {
    id: 'agenda',
    position: 'sidebar',
    region: 'bottom',
    kind: 'entry-row',
    order: 40,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar' },
    default: { id: 'agenda', icon: 'calendar', providedBy: 'builtin' }
  },
  {
    id: 'gateway-health',
    position: 'sidebar',
    region: 'bottom',
    kind: 'entry-row',
    order: 50,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar' },
    default: { id: 'gateway-health', icon: 'pulse', providedBy: 'builtin' }
  },
  {
    id: 'egress',
    position: 'sidebar',
    region: 'bottom',
    kind: 'entry-row',
    order: 60,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar' },
    default: { id: 'egress', icon: 'globe', providedBy: 'builtin' }
  },

  // ── 对话卡右上角那三枚 ──────────────────────────────────────────────────
  {
    id: 'dossier',
    position: 'chat-card',
    region: 'top-right',
    kind: 'entry-row',
    order: 10,
    shape: { icon: true, label: false },
    props: { opens: 'dossier' },
    default: { id: 'dossier', icon: 'book', labelKey: 'surface.agentDossier', providedBy: 'builtin' }
  },
  {
    id: 'pending',
    position: 'chat-card',
    region: 'top-right',
    kind: 'entry-row',
    order: 20,
    shape: { icon: true, label: false },
    props: { opens: 'pending-inbox' },
    default: { id: 'pending', icon: 'inbox', labelKey: 'surface.pendingTickets', providedBy: 'builtin' }
  },
  {
    id: 'mind-pending',
    position: 'chat-card',
    region: 'top-right',
    kind: 'entry-row',
    order: 30,
    shape: { icon: true, label: false },
    props: { opens: 'mind-pending' },
    default: { id: 'mind-pending', icon: 'checklist', labelKey: 'mindPending.title', providedBy: 'builtin' }
  },

  // ── 工作区页签 ────────────────────────────────────────────────────────────
  {
    id: 'files',
    position: 'workspace',
    region: 'tabs',
    kind: 'entry-row',
    order: 10,
    shape: { icon: true, label: true },
    props: { opens: 'files', shells: ['l2'], descriptionKey: 'workbench.actions.files' },
    default: { id: 'files', icon: 'folder-opened', labelKey: 'workbench.tabs.files', providedBy: 'builtin' }
  },
  {
    id: 'agenda',
    position: 'workspace',
    region: 'tabs',
    kind: 'entry-row',
    order: 20,
    shape: { icon: true, label: true },
    props: { opens: 'agenda', shells: ['l1', 'l2'], descriptionKey: 'workbench.actions.agenda' },
    default: { id: 'agenda', icon: 'calendar', labelKey: 'workbench.tabs.agenda', providedBy: 'builtin' }
  },
  {
    id: 'review',
    position: 'workspace',
    region: 'tabs',
    kind: 'entry-row',
    order: 30,
    shape: { icon: true, label: true },
    props: { opens: 'review', shells: ['l2'], descriptionKey: 'workbench.actions.review' },
    default: { id: 'review', icon: 'git-pull-request', labelKey: 'workbench.tabs.review', providedBy: 'builtin' }
  },
  {
    id: 'browser',
    position: 'workspace',
    region: 'tabs',
    kind: 'entry-row',
    order: 40,
    shape: { icon: true, label: true },
    props: { opens: 'browser', shells: ['l1', 'l2'], descriptionKey: 'workbench.actions.browser' },
    default: { id: 'browser', icon: 'globe', labelKey: 'workbench.tabs.browser', providedBy: 'builtin' }
  },
  {
    id: 'terminal',
    position: 'workspace',
    region: 'tabs',
    kind: 'entry-row',
    order: 50,
    shape: { icon: true, label: true },
    props: { opens: 'terminal', shells: ['l1', 'l2'], descriptionKey: 'workbench.actions.terminal' },
    default: { id: 'terminal', icon: 'terminal', labelKey: 'workbench.tabs.terminal', providedBy: 'builtin' }
  },
  {
    id: 'notes',
    position: 'workspace',
    region: 'tabs',
    kind: 'entry-row',
    order: 60,
    shape: { icon: true, label: true },
    props: { opens: 'notes', shells: ['l2'], descriptionKey: 'workbench.actions.notes' },
    default: { id: 'notes', icon: 'notebook', labelKey: 'workbench.tabs.notes', providedBy: 'builtin' }
  }
]

/** 一个 slot 的身份＝位置＋区域＋短名。 */
export function slotKey(position: SlotPosition, region: string, id: string): string {
  return `${position}/${region}/${id}`
}

/** 宿主侧的一册 slot：声明 ＋ 谁填了什么 ＋ 用户关掉了哪些。 */
export interface SlotRegistry {
  /** 某个位置上（某块区域里）的 slot，按顺序。 */
  slotsFor(position: SlotPosition, region?: string): SlotDeclaration[]
  /** 某一格现在有什么（没有就是没有——被关掉了，或还没有人填）。 */
  contentFor(position: SlotPosition, region: string, id: string): SlotContent | undefined
}

export interface SlotRegistryOptions {
  /** 声明表——不给就用出厂那一份；**给了就是用户整份替换过的那一份**。 */
  declaration?: readonly SlotDeclaration[]
  /** 插件填进来的东西（浅的那一步：纯数据）。 */
  contributions?: readonly SlotContribution[]
  /** 用户关掉的那些（出厂的默认插件与插件贡献都算）。 */
  removed?: readonly string[]
}

/**
 * 造一册 slot。
 *
 * 规则只有三条：
 * 1. **声明**决定有哪些位置、区域、顺序与形状——**用户整份替换过就用用户那份**；
 * 2. **填**：出厂默认先填，插件贡献按 slot 覆盖它（插件位＝插件说话）；
 * 3. **关**：用户关掉的那些**真的没有内容**（不是藏起来——屏上那一格消失）。
 */
export function createSlotRegistry(options: SlotRegistryOptions = {}): SlotRegistry {
  const declaration = options.declaration ?? DEFAULT_SLOT_DECLARATION
  const removed = new Set(options.removed ?? [])

  const contentBySlot = new Map<string, SlotContent>()
  for (const slot of declaration) {
    if (slot.default && !removed.has(slot.id)) {
      contentBySlot.set(slotKey(slot.position, slot.region, slot.id), { ...slot.default })
    }
  }
  const declaredKeys = new Set(declaration.map(slot => slotKey(slot.position, slot.region, slot.id)))
  const slots = [...declaration]
  for (const contribution of options.contributions ?? []) {
    if (!contribution?.slotId) {
      continue
    }
    // 填到一个**没有声明**的 slot 上不算数：宿主只认自己声明过的位置。
    let key = declaredKeys.has(contribution.slotId)
      ? contribution.slotId
      : [...declaredKeys].find(k => k.endsWith(`/${contribution.slotId}`))
    if (!key && contribution.position && contribution.region) {
      // 插件**自己声明**一个新 slot（"我要在工作区多一个页签"）：宿主收下它的形状与顺序，
      // 但位置仍得是宿主声明过的那几个。
      if (!POSITIONS.includes(contribution.position)) {
        continue
      }
      const shortId = contribution.slotId.split('/').pop() ?? contribution.slotId
      key = slotKey(contribution.position, contribution.region, shortId)
      if (!declaredKeys.has(key)) {
        slots.push({
          id: shortId,
          position: contribution.position,
          region: contribution.region,
          kind: contribution.kind ?? 'entry-row',
          order: contribution.order ?? 1000,
          shape: contribution.shape ?? { icon: true, label: true },
          props: { shells: contribution.shells ?? ['l1', 'l2'] }
        })
        declaredKeys.add(key)
      }
    }
    if (!key || removed.has(key) || removed.has(contribution.slotId)) {
      continue
    }
    const {
      slotId: _slotId,
      position: _p,
      region: _r,
      kind: _k,
      order: _o,
      shape: _s,
      shells: _sh,
      ...content
    } = contribution
    contentBySlot.set(key, { ...content })
  }

  return {
    slotsFor(position: SlotPosition, region?: string): SlotDeclaration[] {
      return slots
        .filter(slot => slot.position === position && (region === undefined || slot.region === region))
        .sort((a, b) => a.order - b.order)
    },
    contentFor(position: SlotPosition, region: string, id: string): SlotContent | undefined {
      return contentBySlot.get(slotKey(position, region, id))
    }
  }
}

/** 出厂那一份声明里，属于某个位置（某块区域）的那些（便捷出口）。 */
export function defaultSlotsFor(position: SlotPosition, region?: string): SlotDeclaration[] {
  return DEFAULT_SLOT_DECLARATION.filter(
    slot => slot.position === position && (region === undefined || slot.region === region)
  ).sort((a, b) => a.order - b.order)
}


// ─────────────────────────────────────────────────────────────────────────────
// 工作区页签：从声明派生
// ─────────────────────────────────────────────────────────────────────────────

/** 一个壳（l1／l2）。 */
export type ShellLevel = 'l1' | 'l2'

export interface WorkspaceTabDef {
  /** 页签身份——**开放的**：插件带来的页签 id 宿主不预先知道。 */
  id: string
  icon: string
  title: string
  description?: string
}

/**
 * 某个壳里工作区该有哪些页签（按声明的顺序）。
 *
 * **一张顺序 ＋ 按壳过滤**：L1 那一排是 L2 的子序列（今天就是如此），所以不必
 * 给每个壳各写一份清单——那份清单才是"产品替用户枚举"。
 *
 * 插件的页签**自动出现**（用户 11-25 19:5x 拍定，与 DSH 的做法一致）：插件启用，
 * 它的页签就在工作区里；用户可以按 `slots.removed` 关掉。
 */
export function workspaceTabDefsFor(
  registry: SlotRegistry,
  shell: ShellLevel,
  labels: { actions: Record<string, string>; tabs: Record<string, string> }
): WorkspaceTabDef[] {
  return registry
    .slotsFor('workspace', 'tabs')
    .filter(slot => {
      const shells = slot.props.shells
      return Array.isArray(shells) && shells.includes(shell)
    })
    .flatMap(slot => {
      const content = registry.contentFor('workspace', 'tabs', slot.id)
      if (!content) {
        return []
      }
      const descriptionKey = slot.props.descriptionKey
      return [
        {
          id: content.id,
          icon: content.icon ?? 'symbol-misc',
          title: (content.labelKey ? labels.tabs[content.labelKey.split('.').pop() ?? ''] : undefined) ?? content.label ?? content.id,
          description:
            (typeof descriptionKey === 'string' ? labels.actions[descriptionKey.split('.').pop() ?? ''] : undefined) ??
            content.label
        }
      ]
    })
}

/** 用户关掉了哪些 slot（配置 `slots.removed`）——出厂的默认插件与插件贡献都算。 */
export function readRemovedSlots(config: unknown): string[] {
  if (!config || typeof config !== 'object') {
    return []
  }
  const slots = (config as Record<string, unknown>).slots
  const removed = slots && typeof slots === 'object' ? (slots as Record<string, unknown>).removed : null
  return Array.isArray(removed) ? removed.filter((id): id is string => typeof id === 'string') : []
}
