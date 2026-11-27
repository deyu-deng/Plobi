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

import {
  AGENDA_ROUTE,
  ARTIFACTS_ROUTE,
  COMMAND_CENTER_ROUTE,
  CRON_ROUTE,
  DIARY_ROUTE,
  KNOWLEDGE_ROUTE,
  MESSAGING_ROUTE,
  SETTINGS_ROUTE,
  SKILLS_ROUTE
} from '../routes'

import { resolveSlotLabel } from './slot-labels'

/** 宿主留下的三个位置。 */
export type SlotPosition = 'sidebar' | 'chat-card' | 'workspace' | 'settings'

/** 宿主声明过的位置（插件自己声明 slot 时，位置只能从这里挑）。 */
const POSITIONS: readonly SlotPosition[] = ['sidebar', 'chat-card', 'workspace', 'settings']

/**
 * 形状家族——**同一族的挂载点，宿主管的规矩一样**。
 *
 * `entry-row`＝一排格子（侧边栏上面那排、对话卡那三枚、工作区页签条）；
 * `panel`＝一整块面板（侧边栏中间那一大块）；
 * `action`＝**一个独立的行动按钮**——它不是导航行里的一格，**用不同的组件渲染**
 * （DSH 那个「New Session」：透明底＋内描边的一枚整行按钮，与那些图标行一眼分得开）。
 * `status-item`＝**状态栏那一件**（用户 11-25 22:2x：「我们侧边栏底部的图标是**另一种
 * slot**」）——左栏下面那排：图标、无文字标签、**内容来自状态栏管道**（宿主自己的状态）、
 * 点开是**整屏**（`props.opens: 'overlay'`）或**一小张面板**（`'panel'`）。
 *
 * ⚠️ **形状像 ≠ 同一族**：左栏下面那排的**形状**与上面那排一样（一枚小图标按钮，
 * 用户 11-25 12:2x「上面和下面其实差不多」），但**宿主管的东西不同**——上面那排是
 * **导航**（点开去某个页面），下面那排是**状态栏那一件**（内容来自管道、反映一件状态）。
 */
export type SlotKind = 'action' | 'entry-row' | 'panel' | 'status-item' | 'settings-section'

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
  /**
   * 这一格显示**哪几个配置字段**（设置屏那一族：一段＝一组配置字段）。
   *
   * 字段本身来自后端 schema ⇒ 通用表单渲染；插件自己声明的那几个字段由后端并进
   * schema（键在 `plugins.entries.<plugin_id>.*`）⇒ 插件自己读得到。
   */
  keys?: string[]
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
  /**
   * 设置屏那一族：一段＝一组配置字段（用户 11-25 23:3x）⇒ 这里是那几个字段的
   * **完整配置键**（如 `plugins.entries.my-plugin.api_key`）。
   */
  keys?: string[]
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
  /**
   * **作用域**：这一格反映的是**当前这个 Agent** 的状态，还是**全局／整机**的。
   *
   * 用户 11-25 22:4x 定的：「**上面的消息平台和知识库是一般是全局的**」、
   * 「**下面的**一般是**控制目前显示的这个 Agent 的组件**」——
   * **同一族形状、两样作用域**（左栏上面那排与展开态底部那两枚就是这一对）。
   *
   * 默认 `global`（反映全局／整机的那几格）。
   */
  scope?: 'agent' | 'global'
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
  // 全局的那几条**也上到这一排**（用户 11-27 13:0x 拍的分法）：这一排＝**全局的页**。
  {
    id: 'agenda',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 60,
    shape: { icon: true, label: true },
    props: { opens: 'agenda' },
    default: {
      id: 'agenda',
      icon: 'calendar',
      labelKey: 'sidebar.nav.agenda',
      providedBy: 'builtin',
      route: AGENDA_ROUTE
    }
  },
  {
    id: 'cron',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 70,
    shape: { icon: true, label: true },
    props: { opens: 'cron' },
    default: {
      id: 'cron',
      icon: 'clock',
      labelKey: 'sidebar.nav.cron',
      providedBy: 'builtin',
      route: CRON_ROUTE
    }
  },
  {
    id: 'command-center',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 80,
    shape: { icon: true, label: true },
    props: { opens: 'command-center' },
    default: {
      id: 'command-center',
      icon: 'terminal',
      labelKey: 'sidebar.nav.command-center',
      providedBy: 'builtin',
      route: COMMAND_CENTER_ROUTE
    }
  },
  {
    id: 'settings',
    position: 'sidebar',
    region: 'top',
    kind: 'entry-row',
    order: 90,
    shape: { icon: true, label: true },
    props: { opens: 'settings' },
    default: {
      id: 'settings',
      icon: 'gear',
      labelKey: 'sidebar.nav.settings',
      providedBy: 'builtin',
      route: SETTINGS_ROUTE
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
  // ⚠️ 这两格的内容是**宿主自己的面板**（名单要名册的数据、外包要这个 L2）⇒ 它们
  // **没有 view**：屏上按 slot 的 id 给出自己那一块面板。插件带来的那一格**带 view**
  // ⇒ 宿主按它把应用里那一页真的渲染出来。
  {
    id: 'agents',
    position: 'sidebar',
    region: 'middle',
    kind: 'panel',
    order: 10,
    shape: { icon: true, label: true },
    props: { opens: 'agent-roster' },
    default: {
      id: 'agents',
      icon: 'organization',
      labelKey: 'console.tabAgents',
      providedBy: 'builtin'
    }
  },
  {
    id: 'outsourced',
    position: 'sidebar',
    region: 'middle',
    kind: 'panel',
    order: 20,
    shape: { icon: true, label: true },
    props: { opens: 'agent-roster' },
    default: {
      id: 'outsourced',
      icon: 'send',
      labelKey: 'console.tabOutsourced',
      providedBy: 'builtin'
    }
  },

  // ── 侧边栏 · 展开态底部那两枚（**与上面那排同形状**，但作用域＝当前这个 Agent）────
  // 用户 11-25 22:4x：「**下面的**一般是**控制目前显示的这个 Agent 的组件**」——
  // 所以它们与上面那排**同一种形状**（一格图标＋文字），**差别在作用域**（这一族＝
  // 当前这一摊，那一排＝全局的页）。
  {
    id: 'capabilities',
    position: 'sidebar',
    region: 'bottom-nav',
    kind: 'entry-row',
    order: 10,
    shape: { icon: true, label: true },
    scope: 'agent',
    props: { opens: 'capabilities' },
    default: {
      id: 'capabilities',
      icon: 'extensions',
      labelKey: 'sidebar.nav.skills',
      providedBy: 'builtin',
      route: SKILLS_ROUTE
    }
  },
  {
    id: 'agent-artifacts',
    position: 'sidebar',
    region: 'bottom-nav',
    kind: 'entry-row',
    order: 20,
    shape: { icon: true, label: true },
    scope: 'agent',
    props: { opens: 'artifacts' },
    default: {
      id: 'agent-artifacts',
      icon: 'archive',
      labelKey: 'sidebar.nav.artifacts',
      providedBy: 'builtin',
      route: ARTIFACTS_ROUTE
    }
  },

  // ── 侧边栏 · 最下面那排小图标（**另一种 slot 家族**：状态栏那一件）────────────
  // 内容来自**状态栏管道**（宿主自己的状态，`use-statusbar-items.tsx`）⇒ 插件在
  // 「浅」的那一步带不了自己的一枚（它得带前端组件才画得出状态），但**可以替换
  // 某一枚的图标与名字**。`props.opens` 说清点开是**整屏**还是**一小张面板**
  // （用户 11-03 拍的顺序规矩：整屏的排前面，小面板的排后面）。
  //
  // ⚠️ 用户 11-27 13:0x 拍的分法：这一排＝**这一摊的 ＋ 整机的**（代理是这一摊的，
  // 网关／出口是这台机器的）⇒ 整机的两条**不挪走**（窗口那条状态栏在卡片布局里
  // 根本不渲染，挪过去就够不着了）；全局的那几条（设置·命令中心·排程·日程）
  // **上到上面那排**了。
  {
    id: 'agents',
    position: 'sidebar',
    region: 'bottom',
    kind: 'status-item',
    order: 10,
    shape: { icon: true, label: false },
    scope: 'agent',
    props: { pipeline: 'statusbar', opens: 'overlay' },
    default: { id: 'agents', icon: 'organization', providedBy: 'builtin' }
  },
  {
    id: 'gateway-health',
    position: 'sidebar',
    region: 'bottom',
    kind: 'status-item',
    order: 20,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar', opens: 'panel' },
    default: { id: 'gateway-health', icon: 'pulse', providedBy: 'builtin' }
  },
  {
    id: 'egress',
    position: 'sidebar',
    region: 'bottom',
    kind: 'status-item',
    order: 30,
    shape: { icon: true, label: false },
    props: { pipeline: 'statusbar', opens: 'panel' },
    default: { id: 'egress', icon: 'globe', providedBy: 'builtin' }
  },

  // ── 对话卡右上角那三枚 ──────────────────────────────────────────────────
  // 每一枚说清三件事：**显示应用的哪一页**（view）、**角标那个数从哪儿来**（badge）、
  // **在哪些壳里出现**（shells）——档案那一枚只在 L2（指挥没有档案，用户 11-19 22:3x）。
  {
    id: 'dossier',
    position: 'chat-card',
    region: 'top-right',
    kind: 'entry-row',
    order: 10,
    shape: { icon: true, label: false },
    props: { opens: 'dossier', shells: ['l2'] },
    default: {
      id: 'dossier',
      icon: 'book',
      labelKey: 'surface.agentDossier',
      view: 'dossier',
      providedBy: 'builtin'
    }
  },
  {
    id: 'pending',
    position: 'chat-card',
    region: 'top-right',
    kind: 'entry-row',
    order: 20,
    shape: { icon: true, label: false },
    props: { opens: 'pending-inbox', badge: 'pending-inbox' },
    default: {
      id: 'pending',
      icon: 'inbox',
      labelKey: 'surface.pendingTickets',
      view: 'pending',
      providedBy: 'builtin'
    }
  },
  {
    id: 'mind-pending',
    position: 'chat-card',
    region: 'top-right',
    kind: 'entry-row',
    order: 30,
    shape: { icon: true, label: false },
    props: { opens: 'mind-pending', badge: 'mind-pending' },
    default: {
      id: 'mind-pending',
      icon: 'checklist',
      labelKey: 'mindPending.title',
      view: 'mind-pending',
      providedBy: 'builtin'
    }
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
  },

  // ── 设置屏 · 那一段一段（**第四个位置**，用户 11-25 23:3x 拍定）────────────
  // 设置屏是**盖在外壳上的整屏卡**（`OVERLAY_VIEWS` 里有 `settings`）⇒ 它自己一族：
  // **一段＝一组配置字段**（`default.keys`）—— 字段本身来自后端 schema，通用表单渲染。
  //
  // ⚠️ **图标仍归宿主**：那八段的图标是 **Lucide 组件**（声明里只有数据、没有组件）
  // ⇒ 宿主留一张「段 id → 图标」的表（`app/settings/constants.ts`）；插件那一段没有
  // 宿主图标 ⇒ 用它自己声明的 codicon 名字。
  {
    id: 'model',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 10,
    shape: { icon: true, label: true },
    props: {},
    default: {
      id: 'model',
      label: 'Model',
      keys: ['model_context_length', 'fallback_providers'],
      providedBy: 'builtin'
    }
  },
  {
    id: 'chat',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 20,
    shape: { icon: true, label: true },
    props: {},
    default: {
      id: 'chat',
      label: 'Chat',
      keys: ['display.personality', 'timezone', 'display.show_reasoning', 'agent.image_input_mode'],
      providedBy: 'builtin'
    }
  },
  {
    id: 'appearance',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 30,
    shape: { icon: true, label: true },
    props: {},
    default: { id: 'appearance', label: 'Appearance', keys: [], providedBy: 'builtin' }
  },
  {
    id: 'workspace',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 40,
    shape: { icon: true, label: true },
    props: {},
    default: {
      id: 'workspace',
      label: 'Workspace',
      keys: [
        'terminal.cwd',
        'code_execution.mode',
        'terminal.persistent_shell',
        'terminal.env_passthrough',
        'file_read_max_chars'
      ],
      providedBy: 'builtin'
    }
  },
  {
    id: 'safety',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 50,
    shape: { icon: true, label: true },
    props: {},
    default: {
      id: 'safety',
      label: 'Safety',
      keys: [
        'approvals.mode',
        'approvals.timeout',
        'approvals.mcp_reload_confirm',
        'command_allowlist',
        'security.redact_secrets',
        'security.allow_private_urls',
        'browser.allow_private_urls',
        'browser.auto_local_for_private_urls',
        'checkpoints.enabled'
      ],
      providedBy: 'builtin'
    }
  },
  {
    id: 'memory',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 60,
    shape: { icon: true, label: true },
    // 记忆那一段带**分组**（工作记忆／长期记忆／Mind／外部记忆四组）——分组是**宿主的
    // 形状契约**（这一段怎么分块由宿主说了算），字段清单在 `default.keys`。
    props: {
      groups: [
        {
          titleKey: 'settings.groups.memory.working',
          keys: [
            'context.engine',
            'compression.enabled',
            'compression.threshold',
            'compression.target_ratio',
            'compression.protect_last_n'
          ]
        },
        {
          titleKey: 'settings.groups.memory.longTerm',
          keys: [
            'memory.memory_enabled',
            'memory.user_profile_enabled',
            'memory.write_approval',
            'memory.global_char_limit',
            'memory.agent_char_limit',
            'memory.user_char_limit',
            'memory.status_char_limit',
            'memory.conductor_status_char_limit',
            'memory.squeeze_pressure',
            'memory.nudge_interval'
          ]
        },
        {
          titleKey: 'settings.groups.memory.mind',
          keys: ['memory.mind_enabled', 'memory.mind_write_approval', 'memory.mind_write_auto_approve']
        },
        {
          titleKey: 'settings.groups.memory.external',
          keys: ['memory.provider']
        }
      ]
    },
    default: {
      id: 'memory',
      label: 'Memory & Context',
      keys: [
        'context.engine',
        'compression.enabled',
        'compression.threshold',
        'compression.target_ratio',
        'compression.protect_last_n',
        'memory.memory_enabled',
        'memory.user_profile_enabled',
        'memory.write_approval',
        'memory.global_char_limit',
        'memory.agent_char_limit',
        'memory.user_char_limit',
        'memory.status_char_limit',
        'memory.conductor_status_char_limit',
        'memory.squeeze_pressure',
        'memory.nudge_interval',
        'memory.mind_enabled',
        'memory.mind_write_approval',
        'memory.mind_write_auto_approve',
        'memory.provider'
      ],
      providedBy: 'builtin'
    }
  },
  {
    id: 'voice',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 70,
    shape: { icon: true, label: true },
    props: {},
    default: {
      id: 'voice',
      label: 'Voice',
      keys: [
        'tts.provider',
        'stt.enabled',
        'stt.echo_transcripts',
        'stt.provider',
        'voice.auto_tts',
        'tts.edge.voice',
        'tts.openai.model',
        'tts.openai.voice',
        'tts.elevenlabs.voice_id',
        'tts.elevenlabs.model_id',
        'tts.xai.voice_id',
        'tts.xai.language',
        'tts.minimax.model',
        'tts.minimax.voice_id',
        'tts.mistral.model',
        'tts.mistral.voice_id',
        'tts.gemini.model',
        'tts.gemini.voice',
        'tts.neutts.model',
        'tts.neutts.device',
        'tts.kittentts.model',
        'tts.kittentts.voice',
        'tts.piper.voice',
        'stt.local.model',
        'stt.local.language',
        'stt.openai.model',
        'stt.groq.model',
        'stt.mistral.model',
        'stt.elevenlabs.model_id',
        'stt.elevenlabs.language_code',
        'stt.elevenlabs.tag_audio_events',
        'stt.elevenlabs.diarize',
        'voice.record_key',
        'voice.max_recording_seconds'
      ],
      providedBy: 'builtin'
    }
  },
  {
    id: 'advanced',
    position: 'settings',
    region: 'sections',
    kind: 'settings-section',
    order: 80,
    shape: { icon: true, label: true },
    props: {},
    default: {
      id: 'advanced',
      label: 'Advanced',
      keys: [
        'toolsets',
        'terminal.backend',
        'terminal.timeout',
        'terminal.docker_image',
        'terminal.singularity_image',
        'terminal.modal_image',
        'terminal.daytona_image',
        'tool_output.max_bytes',
        'tool_output.max_lines',
        'tool_output.max_line_length',
        'checkpoints.max_snapshots',
        'agent.max_turns',
        'agent.api_max_retries',
        'agent.service_tier',
        'agent.tool_use_enforcement',
        'delegation.model',
        'delegation.provider',
        'delegation.max_iterations',
        'delegation.max_concurrent_children',
        'delegation.child_timeout_seconds',
        'delegation.reasoning_effort',
        'updates.non_interactive_local_changes'
      ],
      providedBy: 'builtin'
    }
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
 *
 * ⚠️ **「关掉」按 slot 的身份记**（位置＋区域＋短名，如 `workspace/tabs/notes`），
 * **不是按短名**——用户 11-25 22:2x：「**我们允许插件可以往多个地方插入内容**」⇒
 * 同一个短名可以在几个 slot 里各有一个（`agenda`：工作区那个页签 ＋ 左栏下面那枚）⇒
 * 关掉**一处**不许把**另一处**一起带走。
 */
export function createSlotRegistry(options: SlotRegistryOptions = {}): SlotRegistry {
  const declaration = options.declaration ?? DEFAULT_SLOT_DECLARATION
  const removed = new Set(options.removed ?? [])

  const contentBySlot = new Map<string, SlotContent>()
  for (const slot of declaration) {
    const key = slotKey(slot.position, slot.region, slot.id)
    if (slot.default && !removed.has(key)) {
      contentBySlot.set(key, { ...slot.default })
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
    if (!key || removed.has(key)) {
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
// 侧边栏中间那一大块：从声明派生
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 左栏中段那一块（**名单 ↔ 外包** 两页来回切）该有哪些页。
 *
 * 与左栏上面那排、工作区那几页签、对话卡那几枚**同一套口径**：清单、顺序、图标、
 * 名字都来自声明表；插件往这一族里加一格 ⇒ **它自动出现**（用户 11-25 22:2x：
 * 「我们允许插件可以往多个地方插入内容」）。
 */
export interface SidebarMiddleItem {
  /** 这一页的身份（开放的：插件带来的 id 宿主不预先知道）。 */
  id: string
  icon: string
  /** 屏上写的名字。 */
  label: string
  /** 这一页显示应用的哪一页（`view`）——宿主按它把那一页真的渲染出来。 */
  view?: string
}

/** 中段那一块该有哪些页（按声明的顺序）。 */
export function sidebarMiddleItems(registry: SlotRegistry, t: unknown): SidebarMiddleItem[] {
  return registry
    .slotsFor('sidebar', 'middle')
    .filter(slot => slot.kind === 'panel')
    .flatMap(slot => {
      const content = registry.contentFor('sidebar', 'middle', slot.id)
      if (!content) {
        return []
      }
      return [
        {
          id: content.id,
          icon: content.icon ?? 'symbol-misc',
          label: resolveSlotLabel(t, content.labelKey) ?? content.label ?? content.id,
          view: content.view ?? (typeof slot.props.view === 'string' ? slot.props.view : undefined)
        }
      ]
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// 设置屏那几段：从声明派生
// ─────────────────────────────────────────────────────────────────────────────

/** 设置屏的**一段**（用户 11-25 23:3x：一段＝一组配置字段）。 */
export interface SettingsSectionDef {
  /** 这一段的 id（`config:<id>` 那个寻址用它）。 */
  id: string
  /** 屏上写的名字（出厂那几段是英文；插件那一段用自己声明的）。 */
  label: string
  /** 这一段显示哪几个配置字段。 */
  keys: string[]
  /** 这一段的**分组**（宿主的形状契约：这一段怎么分块）。 */
  groups?: { titleKey: string; keys: string[] }[]
}

/** 设置屏该有哪些段（按声明的顺序；插件加的那段自动出现）。 */
export function settingsSectionsFor(registry: SlotRegistry): SettingsSectionDef[] {
  return registry
    .slotsFor('settings', 'sections')
    .filter(slot => slot.kind === 'settings-section')
    .flatMap(slot => {
      const content = registry.contentFor('settings', 'sections', slot.id)
      if (!content) {
        return []
      }
      const groups = Array.isArray(slot.props.groups)
        ? (slot.props.groups as { titleKey: string; keys: string[] }[])
        : undefined
      return [
        {
          id: content.id,
          label: content.label ?? content.id,
          keys: Array.isArray(content.keys) ? content.keys : [],
          groups
        }
      ]
    })
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
 *
 * `t` 是宿主那棵 i18n 树——声明里的 `labelKey` 是**点号路径**（`workbench.tabs.files`），
 * 由 `resolveSlotLabel` 走（**一处机制**：对话卡那几枚也走它）。
 */
export function workspaceTabDefsFor(registry: SlotRegistry, shell: ShellLevel, t: unknown): WorkspaceTabDef[] {
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
          title: resolveSlotLabel(t, content.labelKey) ?? content.label ?? content.id,
          description:
            (typeof descriptionKey === 'string' ? resolveSlotLabel(t, descriptionKey) : undefined) ?? content.label
        }
      ]
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// 对话卡右上角那几枚：从声明派生
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 对话卡右上角那一列的**一枚**（档案／待放行／写脑待批／插件带来的那些）。
 *
 * 与工作区那几页签同族：清单来自声明表，插件能替换其中一枚、也能自己加一枚。
 */
export interface ChatCardFaceDef {
  /** 这一枚的身份（开放的：插件带来的 id 宿主不预先知道）。 */
  id: string
  icon: string
  /** 屏上写的名字。 */
  title: string
  /** 这一枚显示应用的哪一页（`view`）——宿主按它把那一页真的渲染出来。 */
  view?: string
  /** 角标那个数来自哪个来源（宿主给数、也让它一直准）。 */
  badge?: string
  /** 在哪些壳里出现（`l1`／`l2`）；不给就是两个壳都有。 */
  shells: string[]
}

/**
 * 对话卡那一列该有哪些枚（按声明的顺序）。
 *
 * **不按壳过滤**（工作区那几页签才滤）：这一列的「在不在」由**抬头**那层决定
 * （指挥那屏没有档案那一枚——用户 11-19 22:3x），那一层知道自己是哪个壳。
 */
export function chatCardFaceDefsFor(registry: SlotRegistry, t: unknown): ChatCardFaceDef[] {
  return registry
    .slotsFor('chat-card', 'top-right')
    .filter(slot => slot.kind === 'entry-row')
    .flatMap(slot => {
      const content = registry.contentFor('chat-card', 'top-right', slot.id)
      if (!content) {
        return []
      }
      const declared = slot.props.shells
      const shells = Array.isArray(declared)
        ? declared.filter((shell): shell is string => typeof shell === 'string')
        : []
      return [
        {
          id: content.id,
          icon: content.icon ?? 'symbol-misc',
          title: resolveSlotLabel(t, content.labelKey) ?? content.label ?? content.id,
          view: content.view ?? (typeof slot.props.view === 'string' ? slot.props.view : undefined),
          badge: typeof slot.props.badge === 'string' ? slot.props.badge : undefined,
          shells: shells.length > 0 ? shells : ['l1', 'l2']
        }
      ]
    })
}

/**
 * 用户关掉了哪些 slot（配置 `slots.removed`）——出厂的默认插件与插件贡献都算。
 *
 * **记的是 slot 的身份**（位置＋区域＋短名，如 `workspace/tabs/notes`／
 * `sidebar/bottom/agenda`），**不是短名**——用户 11-25 22:2x：「**我们允许插件可以
 * 往多个地方插入内容**」⇒ 同一个短名可以在几个 slot 里各有一个 ⇒ 关掉一处不许把
 * 另一处一起带走。
 */
export function readRemovedSlots(config: unknown): string[] {
  if (!config || typeof config !== 'object') {
    return []
  }
  const slots = (config as Record<string, unknown>).slots
  const removed = slots && typeof slots === 'object' ? (slots as Record<string, unknown>).removed : null
  return Array.isArray(removed) ? removed.filter((id): id is string => typeof id === 'string') : []
}
