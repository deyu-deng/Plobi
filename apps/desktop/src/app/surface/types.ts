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
  status: string
  updated: string
  summary: string
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
