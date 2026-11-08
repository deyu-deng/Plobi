import { ListRow, ProviderStatusBadge } from '@/app/settings/primitives'
import { ConnectionControl } from '@/app/settings/connection-control'
import { ProviderBrand } from '@/app/settings/provider-brand'
import { useI18n } from '@/i18n'
import { ChevronRight, Terminal } from '@/lib/icons'
import type { OAuthProvider } from '@/types/plobi'

const PROVIDER_DISPLAY: Record<string, { order: number; title: string }> = {
  nous: { order: 0, title: 'Nous Portal' },
  'openai-codex': { order: 1, title: 'OpenAI OAuth (ChatGPT)' },
  'minimax-oauth': { order: 2, title: 'MiniMax' },
  'qwen-oauth': { order: 3, title: 'Qwen Code' },
  'xai-oauth': { order: 4, title: 'xAI Grok' },
  // Both Anthropic entries sit at the bottom: the API-key path first, then
  // the subscription OAuth path (only works with extra usage credits).
  anthropic: { order: 5, title: 'Anthropic API Key' },
  'claude-code': { order: 6, title: 'Anthropic OAuth: Required Extra Usage Credits to Use Subscription' }
}

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

export const providerTitle = (p: OAuthProvider) => PROVIDER_DISPLAY[p.id]?.title ?? p.name
const orderOf = (p: OAuthProvider) => PROVIDER_DISPLAY[p.id]?.order ?? 99

export const sortProviders = (providers: OAuthProvider[]) =>
  [...providers].sort((a, b) => orderOf(a) - orderOf(b) || a.name.localeCompare(b.name))

export function FeaturedProviderRow({
  onSelect,
  provider
}: {
  onSelect: (provider: OAuthProvider) => void
  provider: OAuthProvider
}) {
  const { t } = useI18n()
  const loggedIn = provider.status?.logged_in

  return (
    <button
      className="group relative flex w-full items-center justify-between gap-4 rounded-lg bg-primary/[0.06] px-3 py-2.5 text-left transition-colors hover:bg-primary/10"
      onClick={() => onSelect(provider)}
      type="button"
    >
      <span aria-hidden className="arc-border arc-reverse arc-nous" />
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <img alt="" className="size-5 shrink-0 rounded" src={assetPath('apple-touch-icon.png')} />
          <span className="text-[length:var(--conversation-text-font-size)] font-semibold">
            {providerTitle(provider)}
          </span>
          {loggedIn ? (
            <ConnectedTag />
          ) : (
            <span className="inline-flex items-center gap-1.5 bg-primary px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.16em] text-primary-foreground">
              <span aria-hidden="true" className="dither inline-block size-2 shrink-0" />
              {t.onboarding.recommended}
            </span>
          )}
        </div>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{t.onboarding.featuredPitch}</p>
      </div>
      <ChevronRight className="size-4 shrink-0 text-primary transition group-hover:translate-x-0.5" />
    </button>
  )
}

function ConnectedTag() {
  const { t } = useI18n()

  return <ProviderStatusBadge state="ok">{t.onboarding.connected}</ProviderStatusBadge>
}

export function KeyProviderRow({ onClick }: { onClick: () => void }) {
  const { t } = useI18n()

  return (
    <ListRow
      action={<ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
      description={t.onboarding.openRouterPitch}
      onActivate={onClick}
      title="OpenRouter"
    />
  )
}

export function ProviderRow({
  onSelect,
  provider
}: {
  onSelect: (provider: OAuthProvider) => void
  provider: OAuthProvider
}) {
  const { t } = useI18n()
  const loggedIn = provider.status?.logged_in
  const Trail = provider.flow === 'external' ? Terminal : ChevronRight

  return (
    <ListRow
      action={(
        <ConnectionControl
          configureLabel={t.settings.providers.subscriptions.configure}
          configureTestId={`provider-configure-${provider.id}`}
          mode="configure"
          onConfigure={() => onSelect(provider)}
          toggleLabel={t.settings.providers.subscriptions.configure}
        />
      )}
      description={t.onboarding.flowSubtitles[provider.flow]}
      // The first cell is the provider's own mark (user 2026-11-08「图标记得找啊」):
      // same table the Settings tabs use, so a provider looks identical in both places.
      icon={<ProviderBrand name={providerTitle(provider)} slug={provider.id} />}
      onActivate={() => onSelect(provider)}
      title={(
        <span className="flex items-center gap-2">
          {loggedIn ? <ConnectedTag /> : null}
          {providerTitle(provider)}
        </span>
      )}
    />
  )
}
