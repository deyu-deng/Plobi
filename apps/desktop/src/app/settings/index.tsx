import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { SkillsView } from '@/app/skills'
import { SearchField } from '@/components/ui/search-field'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import {
  Archive,
  Bell,
  Download,
  Globe,
  Info,
  KeyRound,
  Layers3,
  LayoutGrid,
  Package,
  RefreshCw,
  Send,
  Settings2,
  Upload,
  Users,
  Wrench,
  Zap
} from '@/lib/icons'
import { getPlobiConfigDefaults, getPlobiConfigRecord, savePlobiConfig } from '@/plobi'
import { notifyError } from '@/store/notifications'

import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { OverlayIconButton } from '../overlays/overlay-chrome'
import { OverlayMain, OverlayNav, type OverlayNavGroup, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'
import { SKILLS_ROUTE } from '../routes'

import { AboutSettings } from './about-settings'
import { AppearanceSettings } from './appearance-settings'
import { ConfigSettings } from './config-settings'
import { SECTIONS } from './constants'
import { GatewaySettings } from './gateway-settings'
import { KEYS_VIEWS, KeysSettings, type KeysView } from './keys-settings'
import { NetworkSettings } from './network-settings'
import { NotificationsSettings } from './notifications-settings'
import { OffloadSettings } from './offload-settings'
import { PROVIDER_VIEWS, ProvidersSettings, type ProviderView } from './providers-settings'
import { SessionsSettings } from './sessions-settings'
import { SourcesSettings } from './sources-settings'
import type { SettingsPageProps, SettingsView as SettingsViewId } from './types'

const SETTINGS_VIEWS: readonly SettingsViewId[] = [
  ...SECTIONS.map(s => `config:${s.id}` as SettingsViewId),
  'providers',
  'gateway',
  'network',
  'keys',
  'notifications',
  'sessions',
  'sources',
  'offload',
  'capabilities',
  'about'
]

/**
 * 设置导航的过滤（Qoder 那一屏顶上就有这一格）。两条判据：
 * ① 命中组名 ⇒ 整组上，不带子项（否则展开态会突然多出一堆缩进行）；
 * ② 只命中子项 ⇒ 把那条**提到顶层**——它本来藏在"要点开那一组才看得见"的位置，
 *    搜到了却点不到，等于没搜。
 */
export function filterNavGroups(groups: readonly OverlayNavGroup[], query: string): OverlayNavGroup[] {
  const needle = query.trim().toLowerCase()

  if (!needle) {
    return [...groups]
  }

  const hits = (label: string) => label.toLowerCase().includes(needle)

  return groups.flatMap(group =>
    hits(group.label)
      ? [{ ...group, children: undefined }]
      : (group.children ?? []).filter(child => hits(child.label)).map(child => ({ ...child }))
  )
}

export function SettingsView({ onClose, onConfigSaved, onMainModelChanged }: SettingsPageProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const { hash, pathname, search } = useLocation()

  // MCP moved out of Settings into Capabilities (/skills?tab=mcp). Keep old
  // `/settings?tab=mcp` deep links working — `useRouteEnumParam` would silently
  // coerce the unknown tab to the default view otherwise. Preserve `server=` so
  // an old bookmark still lands on (and highlights) the selected server.
  useEffect(() => {
    const params = new URLSearchParams(search)

    if (params.get('tab') === 'mcp') {
      const server = params.get('server')
      const suffix = server ? `&server=${encodeURIComponent(server)}` : ''
      navigate(`${SKILLS_ROUTE}?tab=mcp${suffix}`, { replace: true })
    }
  }, [navigate, search])

  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'config:model' as SettingsViewId)
  // Providers subnav (Accounts vs API keys) lives in its own param so each
  // sub-view is deep-linkable and survives a refresh.
  const [providerView, setProviderView] = useRouteEnumParam<ProviderView>('pview', PROVIDER_VIEWS, 'accounts')
  const [keysView] = useRouteEnumParam<KeysView>('kview', KEYS_VIEWS, 'tools')

  // Jump to a section + its sub-view in one navigate. Two sequential setters
  // would each read the same stale `search` and the second would clobber the
  // first's `tab` — so the sub-view never opened on narrow screens.
  const openSubView = (tab: SettingsViewId, param: string, value: string, fallback: string) => {
    const params = new URLSearchParams(search)
    params.set('tab', tab)

    if (value === fallback) {
      params.delete(param)
    } else {
      params.set(param, value)
    }

    const qs = params.toString()
    navigate({ hash, pathname, search: qs ? `?${qs}` : '' }, { replace: true })
  }

  const openProviderView = (view: ProviderView) => openSubView('providers', 'pview', view, 'accounts')
  const openKeysView = (view: KeysView) => openSubView('keys', 'kview', view, 'tools')

  const importInputRef = useRef<HTMLInputElement | null>(null)

  const exportConfig = async () => {
    try {
      const cfg = await getPlobiConfigRecord()
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'plobi-config.json'
      a.click()
      URL.revokeObjectURL(url)
      triggerHaptic('success')
    } catch (err) {
      notifyError(err, t.settings.exportFailed)
    }
  }

  const resetConfig = async () => {
    if (!window.confirm(t.settings.resetConfirm)) {
      return
    }

    try {
      await savePlobiConfig(await getPlobiConfigDefaults())
      triggerHaptic('success')
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, t.settings.resetFailed)
    }
  }

  const navGroups: OverlayNavGroup[] = [
    ...SECTIONS.map(s => {
      const view = `config:${s.id}` as SettingsViewId

      return {
        active: activeView === view,
        icon: s.icon,
        id: view,
        label: t.settings.sections[s.id] ?? s.label,
        onSelect: () => setActiveView(view)
      }
    }),
    {
      active: activeView === 'notifications',
      icon: Bell,
      id: 'notifications',
      label: t.settings.nav.notifications,
      onSelect: () => setActiveView('notifications')
    },
    {
      // 全局清单只住这一处（2026-10-20 用户裁定）：技能 / 工具集 / MCP / Hub
      // 四张**全局**表。它是全局真源本身，不是某个 Agent 的减法视图——所以渲染
      // 在设置 overlay 里，而不是跳出去、也不进任何 Agent 的界面。
      active: activeView === 'capabilities',
      // 语义跟着主界面那一格走（四格扩展），家族跟着这一屏走（用户 11-04：「颜色深浅
      // 和样式还是不统一」——这一列原来混了两家：13 枚 Tabler 描边 + 2 枚 codicon 字体，
      // 字号与笔画粗细不是一个量级）。同一列里只许一家。
      icon: LayoutGrid,
      id: 'capabilities',
      label: t.settings.nav.capabilities,
      onSelect: () => setActiveView('capabilities' as SettingsViewId)
    },
    {
      active: activeView === 'providers',
      children: [
        {
          active: activeView === 'providers' && providerView === 'accounts',
          icon: Users,
          id: 'pview:accounts',
          label: t.settings.nav.providerAccounts,
          onSelect: () => openProviderView('accounts')
        },
        {
          active: activeView === 'providers' && providerView === 'keys',
          icon: KeyRound,
          id: 'pview:keys',
          label: t.settings.nav.providerApiKeys,
          onSelect: () => openProviderView('keys')
        },
        {
          active: activeView === 'providers' && providerView === 'desktop',
          icon: Globe,
          id: 'pview:desktop',
          label: t.settings.nav.providerDesktopQuotas,
          onSelect: () => openProviderView('desktop')
        }
      ],
      gapBefore: true,
      icon: Zap,
      id: 'providers',
      label: t.settings.nav.providers,
      onSelect: () => setActiveView('providers')
    },
    {
      active: activeView === 'sources',
      icon: Layers3,
      id: 'sources',
      label: t.settings.nav.sources,
      onSelect: () => setActiveView('sources')
    },
    {
      active: activeView === 'offload',
      icon: Package,
      id: 'offload',
      label: t.settings.nav.offload,
      onSelect: () => setActiveView('offload')
    },
    {
      active: activeView === 'gateway',
      icon: Send,
      id: 'gateway',
      label: t.settings.nav.gateway,
      onSelect: () => setActiveView('gateway')
    },
    {
      active: activeView === 'network',
      icon: Globe,
      id: 'network',
      label: t.settings.nav.network,
      onSelect: () => setActiveView('network')
    },
    {
      active: activeView === 'keys',
      children: [
        {
          active: activeView === 'keys' && keysView === 'tools',
          icon: Wrench,
          id: 'kview:tools',
          label: t.settings.nav.keysTools,
          onSelect: () => openKeysView('tools')
        },
        {
          active: activeView === 'keys' && keysView === 'settings',
          icon: Settings2,
          id: 'kview:settings',
          label: t.settings.nav.keysSettings,
          onSelect: () => openKeysView('settings')
        }
      ],
      icon: KeyRound,
      id: 'keys',
      label: t.settings.nav.apiKeys,
      onSelect: () => setActiveView('keys')
    },
    {
      active: activeView === 'sessions',
      icon: Archive,
      id: 'sessions',
      label: t.settings.nav.archivedChats,
      onSelect: () => setActiveView('sessions')
    },
    {
      active: activeView === 'about',
      gapBefore: true,
      icon: Info,
      id: 'about',
      label: t.settings.nav.about,
      onSelect: () => setActiveView('about')
    }
  ]

  // 导航过滤那一格的查询词（Qoder 的设置导航顶上就有）。
  const [navQuery, setNavQuery] = useState('')

  const navFooter = (
    <>
      <Tip label={t.settings.exportConfig}>
        <OverlayIconButton onClick={() => void exportConfig()}>
          <Download />
        </OverlayIconButton>
      </Tip>
      <Tip label={t.settings.importConfig}>
        <OverlayIconButton
          onClick={() => {
            triggerHaptic('open')
            importInputRef.current?.click()
          }}
        >
          <Upload />
        </OverlayIconButton>
      </Tip>
      <Tip label={t.settings.resetToDefaults}>
        <OverlayIconButton
          className="hover:text-destructive"
          onClick={() => {
            triggerHaptic('warning')
            void resetConfig()
          }}
        >
          <RefreshCw />
        </OverlayIconButton>
      </Tip>
    </>
  )

  return (
    <OverlayView closeLabel={t.settings.closeSettings} onClose={onClose}>
      <OverlaySplitLayout>
        <OverlayNav
          footer={navFooter}
          groups={filterNavGroups(navGroups, navQuery)}
          search={
            <SearchField
              aria-label={t.titlebar.search}
              onChange={setNavQuery}
              placeholder={t.titlebar.search}
              value={navQuery}
            />
          }
        />

        <OverlayMain className="px-0 pb-0">
          {activeView === 'config:appearance' ? (
            <AppearanceSettings />
          ) : activeView === 'about' ? (
            <AboutSettings />
          ) : activeView === 'gateway' ? (
            <GatewaySettings />
          ) : activeView === 'network' ? (
            <NetworkSettings />
          ) : activeView.startsWith('config:') ? (
            <ConfigSettings
              activeSectionId={activeView.slice('config:'.length)}
              importInputRef={importInputRef}
              onConfigSaved={onConfigSaved}
              onMainModelChanged={onMainModelChanged}
            />
          ) : activeView === 'providers' ? (
            <ProvidersSettings onClose={onClose} onViewChange={setProviderView} view={providerView} />
          ) : activeView === 'keys' ? (
            <KeysSettings view={keysView} />
          ) : activeView === 'notifications' ? (
            <NotificationsSettings />
          ) : activeView === 'sources' ? (
            <SourcesSettings />
          ) : activeView === 'offload' ? (
            <OffloadSettings />
          ) : activeView === 'capabilities' ? (
            <SkillsView />
          ) : (
            <SessionsSettings />
          )}
        </OverlayMain>
      </OverlaySplitLayout>
    </OverlayView>
  )
}

export { SettingsView as SettingsPage }
