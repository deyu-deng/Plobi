export interface SurfaceDocMeta {
  key: string
  label: string
  filename: string
  exists: boolean
  chars: number
  updated: string
  path: string
}

export interface SurfaceDocPage extends SurfaceDocMeta {
  fsPath: string
  text: string
  truncated: boolean
}

/**
 * 档案本体（`dossier.json`）里的一个阶段——进度条上的一个节点。
 *
 * 11-09 起档案从 md 退役为结构化记录（WP-DOSSIER-SHAPE）：目标／状况／阶段都在
 * 那一份里，屏上这张卡与喂给 L1 的现状摘要读的是**同一份数据**。
 */
export interface SurfaceStage {
  id: string
  title: string
  detail: string
  done: boolean
}

export interface SurfaceAgent {
  id: string
  name: string
  displayName: string
  category: string
  categoryLabel: string
  role: string
  description: string
  homeLabel: string
  homePath: string
  hasHome: boolean
  workPath: string
  workPaths: string[]
  /** 生命周期状态（`active`／`paused`…）——侧栏那颗点认它，**不是**现状。 */
  status: string
  /** 目标：进度条右上角那枚图标 hover 出来的详情。 */
  goal: string
  /** 现状：一段话。 */
  summary: string
  /** 阶段：进度条上一个节点 ＝ 一个阶段。 */
  stages: SurfaceStage[]
  /** 档案本体在不在（不在 ≠ 读不到，是「还没建立」）。 */
  profileExists: boolean
  /** 档案本体读不出来时的原因（空 = 读得到）。 */
  profileError: string
  updated: string
  milestones: { done: number; total: number }
  docs: SurfaceDocMeta[]
}

export interface SurfaceAgentPage extends Omit<SurfaceAgent, 'docs'> {
  docs: SurfaceDocPage[]
}

export interface SurfaceIndex {
  roster: string
  agents: SurfaceAgent[]
  totals: { agents: number; withHome: number; withoutHome: number }
}
