import {
  SiAlibabacloud,
  SiAnthropic,
  SiDeepseek,
  SiGithubcopilot,
  SiGooglegemini,
  SiHuggingface,
  SiMeta,
  SiMinimax,
  SiMistralai,
  SiMoonshotai,
  SiNvidia,
  SiOllama,
  SiOpenrouter,
  SiQwen,
  SiVercel,
  SiXiaomi
} from '@icons-pack/react-simple-icons'

import { EntityAvatar } from './entity-avatar'

/**
 * Brand mark for one provider card (Accounts / API keys). User 2026-11-08: 「图标记得找啊」.
 *
 * Sources are the pack we already depend on (`@icons-pack/react-simple-icons`), not files we
 * drew: a mark is a brand's own glyph, and the pack ships the vendor's official path. A
 * provider it does not carry falls back to the spec's own fallback — first letter on the
 * brand's neutral tile — rather than a made-up logo. Matched on the *credential* var
 * prefix (the one thing every row has), so the same table serves the Keys tab, whose rows
 * only carry an env var and a label.
 *
 * Not mapped on purpose: OpenAI / Fireworks / Tencent / StepFun / DeepInfra / Nebius /
 * Upstage / Z.AI / CommandCode / Actual / Router — the pack has no official glyph for them
 * here, and a wrong logo is worse than a letter. Add a mapping the day the pack carries one.
 */
const BY_ENV_PREFIX: Array<[string, typeof SiAnthropic, string]> = [
  ['ANTHROPIC', SiAnthropic, '#D97757'],
  ['CLAUDE', SiAnthropic, '#D97757'],
  ['GEMINI', SiGooglegemini, '#8E75B2'],
  ['GOOGLE', SiGooglegemini, '#8E75B2'],
  ['DEEPSEEK', SiDeepseek, '#4D6BFE'],
  ['NVIDIA', SiNvidia, '#76B900'],
  ['MISTRAL', SiMistralai, '#FA520F'],
  ['HF_', SiHuggingface, '#FFD21E'],
  ['HUGGINGFACE', SiHuggingface, '#FFD21E'],
  ['OLLAMA', SiOllama, '#000000'],
  ['OPENROUTER', SiOpenrouter, '#94A3B8'],
  ['DASHSCOPE', SiAlibabacloud, '#FF6A00'],
  ['ALIBABA', SiAlibabacloud, '#FF6A00'],
  ['AI_GATEWAY', SiVercel, '#000000'],
  ['META', SiMeta, '#0467DF'],
  ['MINIMAX', SiMinimax, '#F23F79'],
  ['KIMI', SiMoonshotai, '#16191E'],
  ['MOONSHOT', SiMoonshotai, '#16191E'],
  ['XIAOMI', SiXiaomi, '#FF6900'],
  ['QWEN', SiQwen, '#6950EF'],
  ['GITHUB_COPILOT', SiGithubcopilot, '#000000']
]

/** The same table for rows that carry a provider slug instead of a credential var. */
/**
 * 下标指的是 `BY_ENV_PREFIX` 里的第几行 —— 老表变长时这行忘了跟着挪，导致
 * deepseek 显示 Gemini、openrouter 显示 Ollama（2026-11-19 修）。**加表项时两处一起改。**
 */
const BY_SLUG: Record<string, number> = {
  anthropic: 0, 'claude-code': 0, deepseek: 4, gemini: 2, vertex: 3, nvidia: 5,
  'ollama-cloud': 9, openrouter: 10, alibaba: 12, 'alibaba-cn': 12, 'ai-gateway': 13,
  'meta-ai': 14, minimax: 15, 'minimax-cn': 15, 'minimax-oauth': 15, 'kimi-coding': 16,
  'kimi-coding-cn': 16, xiaomi: 18, 'qwen-oauth': 19, 'qwen-cli': 19, qwen: 19,
  'github-copilot': 20, copilot: 20
}

export function ProviderBrand({
  className,
  envKey = '',
  name,
  slug = ''
}: {
  className?: string
  /** The provider's primary credential var (e.g. `DEEPSEEK_API_KEY`). */
  envKey?: string
  name: string
  /** The provider slug, for rows that never show a credential var. */
  slug?: string
}) {
  const upper = (envKey || '').toUpperCase()
  const bySlug = BY_SLUG[slug.toLowerCase()]

  const hit = bySlug !== undefined
    ? BY_ENV_PREFIX[bySlug]
    : BY_ENV_PREFIX.find(([prefix]) => upper.startsWith(prefix))

  const monogram = (name || envKey || '?').trim().charAt(0).toUpperCase()

  if (!hit) {
    return <EntityAvatar className={className} monogram={monogram} />
  }

  const [, Icon, color] = hit

  return (
    <EntityAvatar
      className={className}
      color={color}
      glyph={<Icon aria-hidden className="size-4" color={color} title="" />}
      monogram={monogram}
    />
  )
}
