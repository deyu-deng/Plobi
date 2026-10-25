import {
  createOpenDeskOfficeRendererViteConfig,
  createOpenDeskOfficeViteAliases
} from '@bitclub.ai/opendesk-office/vite'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))
const base = createOpenDeskOfficeRendererViteConfig()
const packageAliases = createOpenDeskOfficeViteAliases()

// The workspace hoists Univer once; the vendor imports must resolve to that
// same instance or its DI tokens split (ThemeService/SheetsFilterService).
const univerRoot = path.resolve(here, '../../node_modules/@univerjs')

export default defineConfig({
  ...base,
  root: path.join(here, 'genoffice'),
  base: './',
  publicDir: false,
  resolve: {
    ...base.resolve,
    alias: [
      { find: /^@univerjs\/([^/]+)$/u, replacement: path.join(univerRoot, '$1') },
      ...Object.entries(packageAliases).map(([find, replacement]) => ({ find, replacement }))
    ],
    dedupe: [...(base.resolve?.dedupe ?? []), '@univerjs/core', '@univerjs/themes', 'react', 'react-dom']
  },
  server: {
    ...base.server,
    host: '127.0.0.1',
    port: 5175,
    strictPort: true
  },
  build: {
    outDir: path.resolve(here, 'dist/genoffice'),
    emptyOutDir: true,
    rolldownOptions: {
      input: { slides: 'slides.html', word: 'word.html', sheets: 'sheets.html' }
    }
  }
})
