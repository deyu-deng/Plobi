export const NOTES_PREFIX = 'Surface/notes/'

/**
 * 长笔记的骨架解析与目录树。
 *
 * 这里最要紧的一件事：**渲染器和大纲必须用同一份标题清单**。两边都先剥掉
 * frontmatter、都跳过围栏代码块、都按同样的顺序生成 id，点目录才不会跳错地方。
 * 用户在笔记里按「第N章 / N.M 节 / 小节」组织，最长一篇有 272 个标题。
 */

export interface OutlineItem {
  id: string
  level: number
  text: string
}

export interface NoteTreeNode<T> {
  name: string
  key: string
  folders: NoteTreeNode<T>[]
  notes: T[]
}

export interface NoteTree<T> {
  folders: NoteTreeNode<T>[]
  loose: T[]
}

export function stripFrontmatter(text: string): string {
  if (!text.startsWith('---')) return text
  const end = text.indexOf('\n---', 3)
  return end < 0 ? text : text.slice(end + 4).replace(/^\s*\n/, '')
}

/** 标题文字 → 锚点 id 的那部分（`h-` 前缀在外）。渲染与查找共用同一套规则。 */
export function slug(text: string): string {
  const value = text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
  return value || 'section'
}

export function parseOutline(markdown: string): OutlineItem[] {
  const items: OutlineItem[] = []
  const used = new Map<string, number>()
  let fence: string | null = null

  for (const line of stripFrontmatter(markdown).split(/\r?\n/)) {
    const stripped = line.trim()
    const marker = stripped.slice(0, 3)
    if (fence === null && (marker === '```' || marker === '~~~')) {
      fence = marker
      continue
    }
    if (fence !== null) {
      if (marker === fence) fence = null
      continue
    }
    const match = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(stripped)
    if (!match) continue
    const text = match[2].trim()
    const base = slug(text)
    const nth = (used.get(base) ?? 0) + 1
    used.set(base, nth)
    items.push({
      id: nth === 1 ? `h-${base}` : `h-${base}-${nth}`,
      level: match[1].length,
      text
    })
  }
  return items
}

/** 从 `Surface/notes/数学/常微分方程基础.md` 反推「学科 → 子类 → 书」的层级。 */
export function buildNoteTree<T extends { path: string; title: string }>(notes: T[]): NoteTree<T> {
  const folders: NoteTreeNode<T>[] = []
  const loose: T[] = []
  const index = new Map<string, NoteTreeNode<T>>()

  for (const note of notes) {
    const relative = note.path.startsWith(NOTES_PREFIX) ? note.path.slice(NOTES_PREFIX.length) : note.path
    const dirs = relative.split('/').slice(0, -1).filter(Boolean)
    if (dirs.length === 0) {
      loose.push(note)
      continue
    }
    let siblings = folders
    let key = ''
    let node: NoteTreeNode<T> | undefined
    for (const dir of dirs) {
      key = key ? `${key}/${dir}` : dir
      node = index.get(key)
      if (!node) {
        node = { name: dir, key, folders: [], notes: [] }
        index.set(key, node)
        siblings.push(node)
      }
      siblings = node.folders
    }
    if (node) node.notes.push(note)
  }

  const sort = (list: NoteTreeNode<T>[]): void => {
    list.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
    for (const item of list) {
      item.notes.sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN'))
      sort(item.folders)
    }
  }
  sort(folders)
  loose.sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN'))
  return { folders, loose }
}

/** 新建时的起手式：字段给齐，但不替人决定归属与关联（与后端 note_template 同形）。 */
export function noteTemplate(title: string): string {
  const clean = title.trim() || '未命名'
  return `---\ntitle: ${clean}\ntags: []\nbelongs: []\n---\n\n# ${clean}\n\n`
}
