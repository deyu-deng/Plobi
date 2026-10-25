// Build the GenOffice host bundle and the three webview preloads. The host
// bundle is CJS on purpose: the package's dist main mixes in a renderer-only
// module and CommonJS vendor code that an ESM main bundle cannot import.
import { build } from 'esbuild'
import { createOpenDeskOfficeViteAliases } from '@bitclub.ai/opendesk-office/vite'
import { copyFileSync, mkdirSync } from 'node:fs'
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

await build({
  entryPoints: [path.join(appRoot, 'scripts', 'genoffice-host-entry.ts')],
  outfile: path.join(dist, 'host.cjs'),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node20',
  external: ['electron'],
  plugins: [assetPlugin],
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
