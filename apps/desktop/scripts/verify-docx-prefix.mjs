/**
 * docx 前缀容忍的回归验证 —— 直接用渲染器的解析层跑同一份文档的归一化前后两份，
 * 对比 blocks 里 `image` 的数量。
 *
 * 为什么需要它：只看「XML 前缀改对了没」不足以证明修好——`.rels` 漏了、VML 命名空间
 * 漏了，XML 改得再对，渲染器照样一张图都出不来。这个脚本跑的是 `parseDocx` 本身，
 * 是唯一能证明「渲染器认了」的验证。
 *
 * 用法：node scripts/verify-docx-prefix.mjs <docx 路径>
 * 判据：输出里 `图片块：原始 N → 归一化 M`，M > 0 且 M > N。
 */
import { build } from 'esbuild'
import fs from 'node:fs'
import JSZip from 'jszip'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { fixDocxPrefixes, normalizeDocxBuffer } from './docx-prefix-tolerance.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const packageRoot = path.resolve(here, '../../../node_modules/@bitclub.ai/opendesk-office')
const ENGINE = path.join(packageRoot, 'dist', 'vendor', 'packages', 'docx-engine', 'src', 'index.ts')
const CUSTGEOM = path.join(
  packageRoot,
  'dist',
  'vendor',
  'packages',
  'pptx-engine',
  'src',
  'custgeom.ts'
)
const BUNDLE = path.join(here, '.docx-engine.verify.mjs')

const SRC = process.argv[2]

if (!SRC) {
  console.error('用法：node scripts/verify-docx-prefix.mjs <docx 路径>')
  process.exit(2)
}

await build({
  entryPoints: [ENGINE],
  bundle: true,
  format: 'esm',
  outfile: BUNDLE,
  platform: 'node',
  alias: { '@genoffice/pptx-engine/custgeom': CUSTGEOM },
  loader: { '.css': 'empty', '.ttf': 'empty', '.wasm': 'empty' },
  logLevel: 'error'
})

const { parseDocx } = await import(`file://${BUNDLE.replace(/\\/g, '/')}`)

const zip = await JSZip.loadAsync(fs.readFileSync(SRC))

for (const name of Object.keys(zip.files)) {
  if (!/\.(xml|rels)$/iu.test(name)) continue

  const text = await zip.file(name).async('string')
  const fixed = fixDocxPrefixes(text)

  if (fixed !== text) {
    zip.file(name, fixed)
  }
}

const fixedBytes = new Uint8Array(
  await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' })
)
const rawBytes = new Uint8Array(fs.readFileSync(SRC))

const before = await parseDocx(rawBytes)
const after = await parseDocx(fixedBytes)

const tally = doc => {
  const counts = {}

  for (const block of doc.blocks ?? []) {
    counts[block.type] = (counts[block.type] ?? 0) + 1
  }

  return counts
}

const images = doc => (doc.blocks ?? []).filter(b => b.type === 'image').length
const chips = doc =>
  [...new Set((doc.blocks ?? []).filter(b => b.type === 'passthrough').map(b => b.label))].slice(0, 6)

// 第二条路：真正跑的那条 —— 主进程 `loadDocx` 交给渲染进程之前，用
// `normalizeDocxBuffer` 归一化字节。这里直接喂原始字节，验证同一个函数也能让
// 渲染进程的 parseDocx 认出图片（dev / 生产都走这一个出口）。
const normalizedBuffer = await normalizeDocxBuffer(rawBytes)
const viaBuffer = normalizedBuffer ? await parseDocx(new Uint8Array(normalizedBuffer)) : null

console.log('原始：  ', JSON.stringify(tally(before)))
console.log('归一化：', JSON.stringify(tally(after)))
console.log('原始占位：', chips(before))
console.log('剩余占位：', chips(after))
console.log()
console.log(`图片块：原始 ${images(before)} → 归一化 ${images(after)}`)
console.log(`字节路径（loadDocx）：${viaBuffer ? `${images(viaBuffer)} 张图片` : '未触发归一化'}`)

fs.rmSync(BUNDLE, { force: true })

if (images(after) <= images(before)) {
  console.error('FAIL —— 归一化没有让渲染器多认出图片')
  process.exit(1)
}

if (!viaBuffer || images(viaBuffer) < images(after)) {
  console.error('FAIL —— 字节路径（loadDocx 交付前归一化）没有生效')
  process.exit(1)
}

console.log('PASS')
