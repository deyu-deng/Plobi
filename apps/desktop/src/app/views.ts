/**
 * 应用里的「页」与组件的对应表 —— **slot 的内容经它解析**。
 *
 * 为什么要有这张表：工作区的页签与对话卡那几枚都是**插件位**（裁定 239）。
 * 插件声明一个页签（或一枚入口）时，浅的那一步**不许在界面上执行代码**（声明式），
 * 所以那一格的内容得由**宿主**给：宿主按插件声明的 view 找到对应的那一页，
 * 真的把它渲染出来——**不是一个占位**。
 *
 * 这张表也是「深」的那一步的落点：插件自己带前端组件时，只要往这张表里注册一个
 * 渲染器就行，**工作区那几行与抬头那一列一个字都不用改**。
 *
 * ⚠️ 壳里那几份懒加载 import 指向的是**同一个模块**（同一份组件只有一处实现），
 * 这里只是多一张「view → 组件」的对应表。
 */
import { createElement, lazy } from 'react'
import type { ComponentType } from 'react'

import { DossierFace } from './dossier/dossier-panel'
import { MindPendingFace } from './mind-pending-overlay'
import { PendingTicketsFace } from './shell/pending-tickets'
import { PluginFrame } from './shell/plugin-frame'

/**
 * 一个 view 的渲染器。
 *
 * ⚠️ 这些 view 大多都是**浮层页**：各自带 props（`onClose` 之类），本来就没打算被嵌进
 * 页签里。要让"插件的页签"显示真内容，得先有一层**可复用的面板**（把 view 的内容抽
 * 出来，浮层页与页签共用）——那是壳级的一刀，见 `Docs/specs/shell-slots.md`。
 * 这一层先把"view → 组件"的对应表立起来，插件自己带前端组件时往同一张表里注册就行。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ViewRenderer = ComponentType<any>

/** 插件自己的界面那个 view 的前缀（``plugin:<插件目录名>``）。 */
export const PLUGIN_VIEW_PREFIX = 'plugin:'

/**
 * 这里放**不带 props 就能渲染**的那些。
 *
 * 知识库是**样板**（用户 11-25 21:3x 拍定）：它的内容抽成了 `KnowledgePanel`，浮层页与
 * 工作区页签共用同一份实现。其余页面还是"浮层页"（自带 `onClose` 这类 props，本来就没
 * 打算被嵌进页签里）⇒ 它们各自照知识库这个形状拆一次面板之后，才进这张表。
 *
 * **对话卡那三枚**（档案／待放行／写脑待批）也在这张表里——它们是**那一面**的内容，
 * 同样由**声明的 view** 指过来（裁定 239 第四刀）：那一列不再按 id 写死分支。
 */
const VIEW_RENDERERS: Record<string, ViewRenderer> = {
  diary: lazy(async () => ({ default: (await import('./diary')).DiaryView })),
  knowledge: lazy(async () => ({ default: (await import('./knowledge')).KnowledgePanel })),
  dossier: DossierFace,
  pending: PendingTicketsFace,
  'mind-pending': MindPendingFace
}

/** 一个 view 的渲染器（没有就是没有——插件声明了一个宿主没有的 view ⇒ 那一格不渲染）。 */
export function viewRenderer(view: string): ViewRenderer | undefined {
  // 插件自己的界面（深档机制第三刀）：`plugin:<插件目录名>` ⇒ 那个插件的 `ui/` 产物
  // 跑在**独立 origin** 的 iframe 里（`plobi-plugin://`）⇒ 碰不到宿主 DOM 与内存。
  if (view.startsWith(PLUGIN_VIEW_PREFIX)) {
    const dir = view.slice(PLUGIN_VIEW_PREFIX.length).trim()

    if (!dir || dir.includes('/') || dir.includes('\\')) {
      return undefined
    }

    return () => createElement(PluginFrame, { dir, title: dir })
  }

  return VIEW_RENDERERS[view]
}
