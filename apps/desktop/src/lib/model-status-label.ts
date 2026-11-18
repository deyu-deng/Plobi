import { normalize } from '@/lib/text'
import type { ModelOptionProvider } from '@/types/plobi'

/**
 * 一行 provider 在**配置那个宇宙**里的名字。
 *
 * 网关（`bezel`）是**内部名**：用户的配置与屏上写的是**软件名**（`workbuddy`、
 * `qoder`…），机器标识只留在日志／doctor 行 id／路由值里（主人 11-17 18:5x）。
 * 网关那几行一行一个软件（`quotaProviders` 用 hub 名册重排过），`ui_key` 形如
 * `bezel::<软件 id>` ⇒ 那一段就是这一行的身份。
 *
 * 别家 provider 没有这一层 ⇒ 身份就是它的 slug。
 */
export function providerIdentity(
  provider: Pick<ModelOptionProvider, 'slug' | 'ui_key'>,
  model?: string
): string {
  const key = (provider.ui_key ?? '').trim()
  const sep = key.indexOf('::')

  if (sep >= 0) {
    const software = key.slice(sep + 2).trim().toLowerCase()

    if (software) {
      return software
    }
  }

  const slug = (provider.slug ?? '').trim()

  // 没有 ui_key 的网关行（后端原样发来的那几行）：模型 id 的前缀就是那家软件
  // （`workbuddy/hy4free` ⇒ `workbuddy`）。
  if (/^bezel$/i.test(slug) && model) {
    const prefix = model.split('/', 1)[0]?.trim().toLowerCase()

    if (prefix) {
      return prefix
    }
  }

  return slug
}


const REASONING_LABELS: Record<string, string> = {
  none: 'Off',
  minimal: 'Min',
  low: 'Low',
  medium: 'Med',
  high: 'High',
  xhigh: 'Max'
}

export function reasoningEffortLabel(effort: string): string {
  const key = normalize(effort)

  if (!key) {
    return ''
  }

  return REASONING_LABELS[key] ?? effort
}

/** Which model/provider a picker should mark "current". With a live session the
 *  gateway's `model.options` is authoritative; pre-session there is no server
 *  "current", so the sticky composer pick wins over the profile default the
 *  global options query returns — else the checkmark snaps back to the default
 *  and the pick looks ignored. */
export function currentPickerSelection(
  hasSession: boolean,
  store: { model: string; provider: string },
  options?: { model?: string; provider?: string }
): { model: string; provider: string } {
  return {
    model: String((hasSession && options?.model) || store.model || options?.model || ''),
    provider: String((hasSession && options?.provider) || store.provider || options?.provider || '')
  }
}

/** Strip provider prefix and normalize for display. */
export function modelBaseId(model: string): string {
  const trimmed = model.trim()
  const slash = trimmed.lastIndexOf('/')

  return slash >= 0 ? trimmed.slice(slash + 1) : trimmed
}

// Trailing model-id variants that should render as a grayed tag beside the
// name (e.g. "Opus 4.8" + "Fast") rather than collapsing two distinct ids to
// the same display name.
const VARIANT_TAGS: ReadonlyArray<readonly [RegExp, string]> = [
  [/-fast$/i, 'Fast'],
  [/-thinking$/i, 'Thinking'],
  [/-latest$/i, 'Latest']
]

const titleCase = (text: string): string => text.replace(/\b\w/g, char => char.toUpperCase()).trim()

const EXACT_DISPLAY_NAMES: Readonly<Record<string, string>> = {
  'gemini-3-flash-agent': 'Gemini 3.5 Flash (High)',
  'gemini-3.5-flash-low': 'Gemini 3.5 Flash (Medium)',
  'gemini-3.5-flash-extra-low': 'Gemini 3.5 Flash (Low)'
}

function formatTierSuffix(text: string): string {
  return text.replace(/\s+(Low|Medium|High)$/i, (_m, tier: string) => ` (${titleCase(tier.toLowerCase())})`)
}

function normalizeVersionDots(text: string): string {
  return text.replace(/(\d+)-(\d+)/g, '$1.$2')
}

function prettifyBase(base: string): string {
  const exact = EXACT_DISPLAY_NAMES[base.toLowerCase()]

  if (exact) {
    return exact
  }

  if (/^claude-/i.test(base)) {
    const body = normalizeVersionDots(base.replace(/^claude-/i, '')).replace(/-/g, ' ')

    return formatTierSuffix(titleCase(body))
  }

  if (/^gpt-oss-/i.test(base)) {
    const body = base
      .replace(/^gpt-oss-/i, '')
      .replace(/\b(\d+)b\b/gi, '$1B')
      .replace(/-/g, ' ')

    return `GPT-OSS ${formatTierSuffix(titleCase(body))}`.trim()
  }

  if (/^gpt-/i.test(base)) {
    return base.replace(/^gpt-/i, 'GPT-')
  }

  if (/^gemini-/i.test(base)) {
    const body = normalizeVersionDots(base.replace(/^gemini-/i, '')).replace(/-/g, ' ')

    return `Gemini ${formatTierSuffix(titleCase(body))}`.trim()
  }

  if (/^glm-/i.test(base)) {
    const body = normalizeVersionDots(base.replace(/^glm-/i, '')).replace(/-/g, ' ')

    return `GLM ${titleCase(body)}`.trim()
  }

  if (/^minimax-/i.test(base)) {
    const body = normalizeVersionDots(base.replace(/^minimax-/i, '')).replace(/-/g, ' ')

    return `MiniMax ${titleCase(body)}`.trim()
  }

  if (/^mimo-/i.test(base)) {
    const body = normalizeVersionDots(base.replace(/^mimo-/i, '')).replace(/-/g, ' ')

    return `MiMo ${titleCase(body)}`.trim()
  }

  return formatTierSuffix(titleCase(normalizeVersionDots(base).replace(/-/g, ' ')))
}

/** Split a model id into a clean display name plus an optional grayed variant
 *  tag, so distinct ids (e.g. `…-4.8` vs `…-4.8-fast`) don't collapse. */
export function modelDisplayParts(model: string): { name: string; tag: string } {
  let base = modelBaseId(model)
  let tag = ''

  for (const [pattern, label] of VARIANT_TAGS) {
    if (pattern.test(base)) {
      tag = label
      base = base.replace(pattern, '')

      break
    }
  }

  // Drop a trailing date-pin (`…-20251101`) — snapshot noise, not a name.
  base = base.replace(/-\d{8}$/, '')

  return { name: prettifyBase(base) || model.trim() || 'No model', tag }
}

/** Friendly one-line model name for menus and the status bar. */
export function displayModelName(model: string): string {
  return modelDisplayParts(model).name
}

/** Status bar trigger label — model name plus the live session state (effort/fast). */
export function formatModelStatusLabel(
  model: string,
  options?: { fastMode?: boolean; reasoningEffort?: string }
): string {
  const name = displayModelName(model)

  if (!model.trim()) {
    return name
  }

  const parts: string[] = []

  // Fast is shown when the speed=fast param is on (options.fastMode) OR the
  // active model is a `…-fast` variant (fast via a separate model id).
  if (options?.fastMode || /-fast$/i.test(modelBaseId(model))) {
    parts.push('Fast')
  }

  // Always surface the effort (empty = Plobi default of medium) so the
  // current reasoning level is visible at a glance, not just when non-default.
  parts.push(reasoningEffortLabel(options?.reasoningEffort ?? '') || 'Med')

  return `${name} · ${parts.join(' ')}`
}
