import type { ModelOptionProvider } from '@/types/plobi'

/**
 * R-017 — humanize the model selector. The gateway's /v1/models catalog speaks
 * in internal vocabulary (`AIGW`, `CUSTOM ENDPOINT`, `mock/...`); users get
 * software/vendor names instead. Display-only: slugs are preserved so model
 * switching, presets and visibility keys keep their stable identity.
 */

const AIGW_SOFTWARE_PROVIDERS: ReadonlyArray<readonly [RegExp, string]> = [
  [/workbuddy/i, 'WorkBuddy'],
  [/antigravity/i, 'Antigravity']
]

/** Well-known direct-API vendors, keyed by slug (exact match first, then substring). */
const VENDOR_LABELS: ReadonlyArray<readonly [string, string]> = [
  ['deepseek', 'DeepSeek'],
  ['minimax', 'MiniMax'],
  ['moonshot', 'Moonshot AI'],
  ['kimi', 'Kimi'],
  ['zhipu', '智谱 GLM'],
  ['glm', '智谱 GLM'],
  ['qwen', 'Qwen'],
  ['openai', 'OpenAI'],
  ['anthropic', 'Anthropic'],
  ['gemini', 'Google Gemini']
]

/** Internal vocabulary that must never surface as a group title. */
const INTERNAL_WORDS = /\b(aigw|custom\s*endpoint|custom|endpoint|gateway|aggregator)\b/gi

function titleCaseSlug(slug: string): string {
  return slug
    .split(/[-_.\s]+/)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function vendorLabelForSlug(slug: string): string | undefined {
  const key = slug.toLowerCase()

  for (const [needle, label] of VENDOR_LABELS) {
    if (key === needle || key.includes(needle)) {
      return label
    }
  }

  return undefined
}

export function humanizeProviderName(provider: Pick<ModelOptionProvider, 'name' | 'slug'>): string {
  const slug = (provider.slug ?? '').trim()
  const name = (provider.name ?? '').trim()

  // 1. aigw-aggregated software keeps its own brand (WorkBuddy / Antigravity).
  for (const [pattern, label] of AIGW_SOFTWARE_PROVIDERS) {
    if (pattern.test(slug) || pattern.test(name)) {
      return label
    }
  }

  // 2. Direct-API providers show the vendor's own name.
  const vendor = vendorLabelForSlug(slug)

  if (vendor) {
    return vendor
  }

  // 3. Otherwise strip internal vocabulary from whatever the gateway sent.
  const stripped = name.replace(INTERNAL_WORDS, ' ').replace(/\s+/g, ' ').trim()

  if (stripped) {
    return stripped
  }

  // 4. Last resort: derive from the slug so no group is ever untitled.
  return titleCaseSlug(slug || 'Models')
}

/** `mock/echo` and friends are developer fixtures, not selectable models (R-017). */
export function isMockModelId(modelId: string): boolean {
  return modelId.trim().toLowerCase().startsWith('mock/')
}

export function isMockProvider(provider: Pick<ModelOptionProvider, 'slug'>): boolean {
  return provider.slug.trim().toLowerCase() === 'mock'
}

/**
 * Full display pipeline for the selector's provider list: drop the mock
 * provider, drop mock/ models from every provider, and humanize group names.
 */
export function humanizeProviders(providers: readonly ModelOptionProvider[]): ModelOptionProvider[] {
  return providers
    .filter(provider => !isMockProvider(provider))
    .map(provider => {
      const models = (provider.models ?? []).filter(model => !isMockModelId(model))

      return { ...provider, models, name: humanizeProviderName(provider) }
    })
    .filter(provider => (provider.models ?? []).length > 0)
}
