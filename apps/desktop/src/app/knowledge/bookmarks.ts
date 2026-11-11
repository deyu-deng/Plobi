/**
 * 书签（Obsidian 的 bookmarks 那一族）。
 *
 * 落在**本机**（localStorage），不进笔记仓：书签是「我常翻这几篇」的人读面状态，
 * 不是笔记内容 —— 它不该跟着笔记一起进 git，也不该被别的机器同步过去。
 */

const STORAGE_KEY = 'plobi.notes.bookmarks'

function read(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)

    if (!raw) return []

    const parsed = JSON.parse(raw)

    if (!Array.isArray(parsed)) return []

    return parsed.filter(item => typeof item === 'string')
  } catch {
    // 存储被禁（无痕模式）／存的不是数组 ⇒ 当作没有书签，不抛。
    return []
  }
}

function write(paths: string[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(paths))
  } catch {
    // 存不进去就算了：书签是便利功能，不该因为它把保存正文那一路带崩。
  }
}

/** 现在的书签（按加进去的顺序）。 */
export function readBookmarks(): string[] {
  return read()
}

/** 加/去一个书签，返回加完之后的列表。 */
export function toggleBookmark(path: string): string[] {
  const current = read()
  const next = current.includes(path) ? current.filter(item => item !== path) : [...current, path]

  write(next)

  return next
}
