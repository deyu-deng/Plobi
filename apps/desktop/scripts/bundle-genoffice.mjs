// Build the GenOffice host bundle and the three webview preloads. The host
// bundle is CJS on purpose: the package's dist main mixes in a renderer-only
// module and CommonJS vendor code that an ESM main bundle cannot import.
import { build } from 'esbuild'
import { createOpenDeskOfficeViteAliases } from '@bitclub.ai/opendesk-office/vite'

import { patchDocxParser } from './docx-prefix-tolerance.mjs'
import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, promises as fsPromises } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const appRoot = path.resolve(here, '..')
const repoRoot = path.resolve(appRoot, '../..')
const packageRoot = path.join(repoRoot, 'node_modules', '@bitclub.ai', 'opendesk-office')
const packageRequire = createRequire(path.join(packageRoot, 'package.json'))
const harfbuzzDist = path.dirname(packageRequire.resolve('harfbuzzjs'))
const dist = path.join(appRoot, 'dist', 'genoffice')
mkdirSync(dist, { recursive: true })

// 产物契约：dist/genoffice 必须同时有 html（vite build 产）和 host/preload（本脚本产）。
// 历史上 `dev:electron` 只调本脚本 ⇒ 目录里只有 cjs、没有 html，而 genoffice-editor
// 的 builtEditorUrl 优先用 dist 里的 html ⇒ Office 页签必然白屏（10-31 用户报的故障，
// 当时 5175 的 dev server 明明通着，代码却去读一个不存在的 file:// 页面）。
// 所以缺 html 就先把 vite build 补上；顺序不能反——vite 的 emptyOutDir 会清空目录，
// 先写 cjs 再 build 会把 cjs 一起清掉。
const PAGES = ['sheets.html', 'slides.html', 'word.html']
const missingPages = () => PAGES.filter(page => !existsSync(path.join(dist, page)))

if (missingPages().length) {
  console.log(`genoffice: html missing (${missingPages().join(', ')}) — running vite build first`)
  execFileSync(
    process.execPath,
    [
      path.join(repoRoot, 'node_modules', 'vite', 'bin', 'vite.js'),
      'build',
      '--config',
      path.join(appRoot, 'genoffice.vite.config.ts')
    ],
    { cwd: appRoot, stdio: 'inherit' }
  )
}

const stillMissing = missingPages()

if (stillMissing.length) {
  throw new Error(
    `genoffice: dist is incomplete — missing ${stillMissing.join(', ')} in ${dist}. ` +
      'The Office tabs load these pages directly; without them the editor is a blank pane.'
  )
}

const aliases = createOpenDeskOfficeViteAliases()
const assetPlugin = {
  name: 'opendesk-assets',
  setup(build) {
    build.onResolve({ filter: /^@opendesk-office\/harfbuzz-wasm\?asset$/ }, () => ({
      path: 'harfbuzz-wasm',
      namespace: 'genoffice-asset'
    }))
    build.onLoad({ filter: /.*/, namespace: 'genoffice-asset' }, () => ({
      contents: "const path = require('node:path'); const p = path.join(__dirname, 'harfbuzz.wasm'); module.exports = { default: p };",
      loader: 'js'
    }))
  }
}

const aliasPlugin = {
  name: 'opendesk-aliases',
  setup(build) {
    build.onResolve({ filter: /.*/ }, args => {
      const target = aliases[args.path]
      return target ? { path: target } : null
    })
  }
}

// OfficeCLI (and some other writers) serialize SpreadsheetML with a prefix on
// every main-namespace element (`<x:sheet>`). The stock gateway's regexes only
// match the default namespace and report "no readable worksheets". Strip the
// prefix from the XML strings it reads; the rest of the package is untouched.
const xlsxPrefixTolerancePlugin = {
  name: 'xlsx-prefix-tolerance',
  setup(build) {
    build.onLoad({ filter: /[\\/]dist[\\/]sheets-main\.js$/ }, async args => {
      const source = await fsPromises.readFile(args.path, 'utf8')
      const needle = 'const zip = await createBufferEntrySource(buffer);'
      const occurrences = source.split(needle).length - 1

      if (occurrences !== 1) {
        throw new Error(`xlsx prefix patch: expected one readBasicWorkbook needle, found ${occurrences}`)
      }

      const injection = `${needle}
  const __plobiReadText = zip.readText.bind(zip);
  zip.readText = async (name) => {
    const text = await __plobiReadText(name);
    if (!/^xl\\/.*\\.xml$/i.test(name)) return text;
    const main = /xmlns:([A-Za-z_][\\w.-]*)="http:\\/\\/schemas\\.openxmlformats\\.org\\/spreadsheetml\\/2006\\/main"/.exec(text);
    if (!main) return text;
    const prefix = main[1];
    return text
      .replace(new RegExp("<" + prefix + ":", "g"), "<")
      .replace(new RegExp("</" + prefix + ":", "g"), "</");
  };`

      return { contents: source.replace(needle, injection), loader: 'js' }
    })
  }
}

// docx 前缀容忍：有些写入方把 WordprocessingML 序列化成 `<ns0:p>` 而不是 `<w:p>`，
// 而 GenOffice 的解析器按字面匹配 `'w:p'` —— 这类文档在它眼里等于空文档：字体全塌、
// 图片塌成「图片 N」、域塌成 `Field (ADDIN)`。解析发生在主进程（host.cjs），入口是
// `dist/main.js`（`word-main.js` 再转手到它），所以补在这一侧；逻辑见
// `docx-prefix-tolerance.mjs`。
const docxPrefixTolerancePlugin = {
  name: 'docx-prefix-tolerance',
  setup(build) {
    build.onLoad({ filter: /[\\/]dist[\\/]main\.js$/ }, async args => {
      const source = await fsPromises.readFile(args.path, 'utf8')

      return { contents: patchDocxParser(source), loader: 'js' }
    })
  }
}

await build({
  entryPoints: [path.join(appRoot, 'scripts', 'genoffice-host-entry.ts')],
  outfile: path.join(dist, 'host.cjs'),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node20',
  external: ['electron'],
  plugins: [xlsxPrefixTolerancePlugin, docxPrefixTolerancePlugin, assetPlugin],
  loader: { '.css': 'empty', '.ttf': 'empty', '.wasm': 'empty', '.png': 'empty' },
  define: { 'process.env.NODE_ENV': '"production"' },
  logLevel: 'info'
})

for (const [kind, source] of [
  ['slides', path.join(packageRoot, 'src', 'preload.ts')],
  ['word', path.join(packageRoot, 'src', 'word-preload.ts')],
  ['sheets', path.join(packageRoot, 'src', 'sheets-preload.ts')]
]) {
  await build({
    entryPoints: [source],
    outfile: path.join(dist, `${kind}-preload.cjs`),
    bundle: true,
    platform: 'node',
    format: 'cjs',
    target: 'node20',
    external: ['electron'],
    plugins: [aliasPlugin, assetPlugin],
    loader: { '.css': 'empty', '.ttf': 'empty', '.wasm': 'empty', '.png': 'empty' },
    define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'info'
  })
}

copyFileSync(
  path.join(packageRoot, 'dist', 'vendor', 'wasm', 'ironcalc.wasm'),
  path.join(dist, 'ironcalc.wasm')
)
copyFileSync(path.join(harfbuzzDist, 'harfbuzz.wasm'), path.join(dist, 'harfbuzz.wasm'))
console.log(`genoffice host/preloads written to ${dist}`)
