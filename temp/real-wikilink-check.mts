import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { findWikiLinks } from '../apps/desktop/src/app/knowledge/wikilink'
import { buildNoteLinkIndex, resolveWikiNote } from '../apps/desktop/src/app/knowledge/wikilink'

const ROOT = 'D:/Data/AppData/plobi/notes'
const notes: { path: string; title: string }[] = []
const walk = (dir: string): void => {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) { if (!name.startsWith('.')) walk(full) }
    else if (name.endsWith('.md')) notes.push({ path: `Surface/notes/${full.slice(ROOT.length + 1).replace(/\\/g, '/')}`, title: name })
  }
}
walk(ROOT)
const index = buildNoteLinkIndex(notes)

let total = 0, resolvable = 0, anchors = 0, embeds = 0
const broken: string[] = []
for (const note of notes) {
  const text = readFileSync(join(ROOT, note.path.slice('Surface/notes/'.length)), 'utf-8')
  for (const link of findWikiLinks(text)) {
    total++
    if (link.kind === 'embed') { embeds++; continue }
    if (link.kind === 'anchor') { anchors++; continue }
    if (resolveWikiNote(link, index)) resolvable++
    else broken.push(`${note.path}: [[${link.target}]]`)
  }
}
console.log(`笔记 ${notes.length} 篇 / wikilink ${total} 个`)
console.log(`  跨文件可解析: ${resolvable}`)
console.log(`  同篇锚点: ${anchors}`)
console.log(`  嵌入: ${embeds}`)
console.log(`  解析不到的跨文件链接 ${broken.length} 个:`)
for (const b of broken.slice(0, 12)) console.log('   ', b)
