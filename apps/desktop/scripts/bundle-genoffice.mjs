// Build the GenOffice host bundle and the three webview preloads. The host
// bundle is CJS on purpose: the package's dist main mixes in a renderer-only
// module and CommonJS vendor code that an ESM main bundle cannot import.
import { build } from 'esbuild'
import { createOpenDeskOfficeViteAliases } from '@bitclub.ai/opendesk-office/vite'
import { copyFileSync, mkdirSync, promises as fsPromises } from 'node:fs'
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

await build({
  entryPoints: [path.join(appRoot, 'scripts', 'genoffice-host-entry.ts')],
  outfile: path.join(dist, 'host.cjs'),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node20',
  external: ['electron'],
  plugins: [xlsxPrefixTolerancePlugin, assetPlugin],
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
