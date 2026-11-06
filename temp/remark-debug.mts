import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkRehype from 'remark-rehype'
import { remarkWikilink } from '../apps/desktop/src/app/knowledge/remark-wikilink'

const clean = (v: unknown) => JSON.stringify(v, (k, x) => (k === 'position' ? undefined : x), 1)

const src = '看 [[Creo]] 这个\n\n第二段 [[#ch1|1. 发展历程]] 和 [[CATIA]]\n\n```\n[[代码里的]]\n```'
const proc = unified().use(remarkParse)
const tree = proc.parse(src)

remarkWikilink()(tree as never)
console.log('=== mdast (插件后) ===')
console.log(clean(tree).slice(0, 2200))

const hast = remarkRehype()(tree as never)
console.log('\n=== hast（关键片段）===')
console.log(clean(hast).slice(0, 1800))
