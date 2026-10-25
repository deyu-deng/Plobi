// Build the GenOffice host bundle and its webview preload. The host bundle is
// CJS on purpose: the package's dist main mixes in a renderer-only module and
// CommonJS vendor code that an ESM main bundle cannot import.
import { build } from 'esbuild'
import { createOpenDeskOfficeSheetsViteAliases } from '@bitclub.ai/opendesk-office/vite'
import { copyFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const appRoot = path.resolve(here, '..')
const repoRoot = path.resolve(appRoot, '../..')
const dist = path.join(appRoot, 'dist', 'genoffice')
mkdirSync(dist, { recursive: true })

const aliases = createOpenDeskOfficeSheetsViteAliases()
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
  loader: { '.css': 'empty', '.ttf': 'empty', '.wasm': 'empty', '.png': 'empty' },
  define: { 'process.env.NODE_ENV': '"production"' },
  logLevel: 'info'
})

await build({
  entryPoints: [
    path.join(repoRoot, 'node_modules', '@bitclub.ai', 'opendesk-office', 'src', 'sheets-preload.ts')
  ],
  outfile: path.join(dist, 'sheets-preload.cjs'),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node20',
  external: ['electron'],
  plugins: [aliasPlugin],
  loader: { '.css': 'empty', '.ttf': 'empty', '.wasm': 'empty', '.png': 'empty' },
  define: { 'process.env.NODE_ENV': '"production"' },
  logLevel: 'info'
})

copyFileSync(
  path.join(repoRoot, 'node_modules', '@bitclub.ai', 'opendesk-office', 'dist', 'vendor', 'wasm', 'ironcalc.wasm'),
  path.join(dist, 'ironcalc.wasm')
)
console.log(`genoffice host/preload written to ${dist}`)
