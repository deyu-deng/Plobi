export interface VaultDocMeta {
  key: string
  label: string
  filename: string
  exists: boolean
  chars: number
  updated: string
  path: string
}

export interface VaultDocPage extends VaultDocMeta {
  fsPath: string
  text: string
  truncated: boolean
}

export interface VaultAgent {
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
  docs: VaultDocMeta[]
}

export interface VaultAgentPage extends Omit<VaultAgent, 'docs'> {
  docs: VaultDocPage[]
}

export interface VaultIndex {
  roster: string
  agents: VaultAgent[]
  totals: { agents: number; withHome: number; withoutHome: number }
}
