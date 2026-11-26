import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import App from './app'
import { SlotRegistryProvider } from './app/shell/slot-registry-context'
import { ErrorBoundary } from './components/error-boundary'
import { HapticsProvider } from './components/haptics-provider'
import { I18nProvider } from './i18n'
import { installClipboardShim } from './lib/clipboard'
import { queryClient } from './lib/query-client'
import { ThemeProvider } from './themes/context'

installClipboardShim()

// Dev-only: install __PERF_DRIVE__ + __PERF_PROBE__ on window so the
// scripts/ harnesses can drive a synthetic stream + record render cost.
// Tree-shaken out of production builds. (Uses MODE rather than DEV because
// our Vite setup currently bundles with PROD=true even in `vite dev`; see
// scripts/dev-no-hmr.mjs for the surrounding workarounds.)
if (import.meta.env.MODE !== 'production') {
  import('./app/chat/perf-probe')
}

// R-083 — this module is the whole application mount, dynamically imported by
// src/main.tsx so that a break anywhere in the module graph lands on the retry
// card in src/lib/boot-recovery.ts instead of a white window. Keep it free of
// top-level work that must happen before the catch: `mountApp` is only called
// once the graph has loaded.
export function mountApp(): void {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary label="root">
        <QueryClientProvider client={queryClient}>
          <I18nProvider>
            <ThemeProvider>
              <HapticsProvider>
                <HashRouter>
                  {/*
                   * **一册 slot**（界面外壳的挂载点——裁定 239）：插件填进来的那些 ＋
                   * 用户关掉的那些，应用根部拉一次就够 ⇒ 侧边栏、工作区那几页签、
                   * 对话卡那一列读的是**同一册**（三处各拉一遍时它们可能不同步）。
                   */}
                  <SlotRegistryProvider>
                    <App />
                  </SlotRegistryProvider>
                </HashRouter>
              </HapticsProvider>
            </ThemeProvider>
          </I18nProvider>
        </QueryClientProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
