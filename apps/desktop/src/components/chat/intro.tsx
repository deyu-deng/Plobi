import { useState } from 'react'

import { PlobiWordmark } from '@/components/brand-mark'
import { capitalize, normalize } from '@/lib/text'

import introCopyJsonl from './intro-copy.jsonl?raw'

export type IntroCopy = {
  headline: string
  body?: string
}

type IntroCopyRecord = IntroCopy & {
  personality: string
  locale?: string
}

export type IntroProps = {
  personality?: string
  seed?: number
  locale?: string
}

/** 没有 `locale` 字段的旧行是英文——文案池先于多语言存在。 */
const DEFAULT_COPY_LOCALE = 'en'

const NEUTRAL_PERSONALITIES = new Set(['', 'default', 'none', 'neutral'])

const FALLBACK_COPY: IntroCopy[] = [
  {
    headline: 'What are we moving today?',
    body: "Send a bug, branch, plan, or rough idea. I'll inspect the repo and turn it into the next concrete step."
  },
  {
    headline: "What's on your mind?",
    body: "Bring the code, question, or stuck part. I'll read the room before making changes."
  },
  {
    headline: 'What should Plobi look at?',
    body: "Send the task, failing path, or half-formed plan. I'll help turn it into action."
  },
  {
    headline: 'Where should we start?',
    body: "Bring the problem, goal, or file. I'll inspect first and keep the next step concrete."
  },
  {
    headline: 'What needs attention?',
    body: "Send the context you have. I'll help sort it into a plan or a fix."
  }
]

function normalizeKey(value?: string): string {
  return normalize(value)
}

function titleize(value: string): string {
  return value
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map(capitalize)
    .join(' ')
}

function isIntroCopyRecord(value: unknown): value is IntroCopyRecord {
  if (!value || typeof value !== 'object') {
    return false
  }

  const record = value as Record<string, unknown>

  if (typeof record.personality !== 'string' || !record.personality.trim()) {
    return false
  }

  if (typeof record.headline !== 'string' || !record.headline.trim()) {
    return false
  }

  if (record.body === undefined) {
    return true
  }

  return typeof record.body === 'string' && Boolean(record.body.trim())
}

/**
 * 文案池 → `locale` → `personality` → 句子。
 *
 * 一行一句 JSON。`locale` 是后来补的一维：没写这一维的旧行按英文算，所以池子里
 * 那 75 行英文原文一个字不用改。
 */
function parseIntroCopy(raw: string): Record<string, Record<string, IntroCopy[]>> {
  const byLocale: Record<string, Record<string, IntroCopy[]>> = {}

  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim()

    if (!trimmed) {
      continue
    }

    try {
      const parsed: unknown = JSON.parse(trimmed)

      if (!isIntroCopyRecord(parsed)) {
        continue
      }

      const locale = normalizeKey(parsed.locale) || DEFAULT_COPY_LOCALE
      const key = normalizeKey(parsed.personality)

      byLocale[locale] ??= {}
      byLocale[locale][key] ??= []
      byLocale[locale][key].push({
        headline: parsed.headline.trim(),
        body: parsed.body?.trim()
      })
    } catch {
      // Bad generated copy should not break the whole desktop app.
    }
  }

  return byLocale
}

const INTRO_COPY_BY_LOCALE = parseIntroCopy(introCopyJsonl)

function neutralCopy(locale: string): IntroCopy[] {
  const local = INTRO_COPY_BY_LOCALE[locale] ?? {}
  const english = INTRO_COPY_BY_LOCALE[DEFAULT_COPY_LOCALE] ?? {}

  return local.none ?? local.default ?? english.none ?? english.default ?? FALLBACK_COPY
}

function fallbackCopyForPersonality(personalityKey: string): IntroCopy[] {
  if (NEUTRAL_PERSONALITIES.has(personalityKey)) {
    return neutralCopy(DEFAULT_COPY_LOCALE)
  }

  const label = titleize(personalityKey)

  return [
    {
      headline: `${label} mode is on. What should we work on?`,
      body: "Send the task, file, or rough idea. I'll use your configured voice and keep the work grounded in this repo."
    },
    {
      headline: `What does ${label} Plobi need to see?`,
      body: "Bring the context or the stuck part. I'll adapt to your configured personality."
    },
    {
      headline: `${label} mode is ready.`,
      body: "Send the problem, file, or idea. I'll follow the personality you've configured."
    },
    {
      headline: `What should ${label} Plobi tackle?`,
      body: "Drop the task here. I'll keep the work grounded in the repo."
    },
    {
      headline: 'Where should we begin?',
      body: `Give me the context and I'll answer in ${label} mode.`
    }
  ]
}

function copiesFor(locale: string, personalityKey: string): IntroCopy[] {
  if (NEUTRAL_PERSONALITIES.has(personalityKey)) {
    return neutralCopy(locale)
  }

  return (
    INTRO_COPY_BY_LOCALE[locale]?.[personalityKey] ??
    INTRO_COPY_BY_LOCALE[DEFAULT_COPY_LOCALE]?.[personalityKey] ??
    fallbackCopyForPersonality(personalityKey)
  )
}

function pickCopy(copies: IntroCopy[], seed = 0): IntroCopy {
  return copies[Math.abs(seed) % copies.length] || FALLBACK_COPY[0]
}

/**
 * 文案池的公开入口：按语言与人格解出一句。
 *
 * `locale` 没有自己那一份时退回英文那一份——宁可英文，不许空屏。
 */
export function resolveIntroCopy(options: { personality?: string; seed?: number; locale?: string } = {}): IntroCopy {
  const personalityKey = normalizeKey(options.personality)
  const locale = normalizeKey(options.locale) || DEFAULT_COPY_LOCALE

  return pickCopy(copiesFor(locale, personalityKey), options.seed ?? 0)
}

/** 当前语言下的招呼语候选——屏上那句就是从这里头挑的。 */
export function introGreetingCandidates(locale?: string): string[] {
  return copiesFor(normalizeKey(locale) || DEFAULT_COPY_LOCALE, '').map(copy => copy.headline)
}

/** 挂载时定一句，不随重渲染变（重渲染就换一句，屏上的字会跳）。 */
export function useIntroGreeting(options: { personality?: string; seed?: number; locale?: string } = {}): string {
  const [mountSeed] = useState(() => Math.floor(Math.random() * 100000))

  return resolveIntroCopy({ ...options, seed: mountSeed + (options.seed ?? 0) }).headline
}

export function Intro({ personality, seed, locale }: IntroProps) {
  const [mountSeed] = useState(() => Math.floor(Math.random() * 100000))
  const copy = resolveIntroCopy({ personality, seed: mountSeed + (seed ?? 0), locale })

  return (
    <div
      className="pointer-events-none flex w-full min-w-0 flex-col items-center justify-center px-0.5 py-6 text-center text-muted-foreground sm:px-6 lg:px-8"
      data-slot="aui_intro"
    >
      <div className="w-full min-w-0">
        <div className="mx-auto mb-1 w-[calc(100%-1rem)]">
          <PlobiWordmark className="w-full" />
        </div>

        <p className="m-0 text-center leading-normal tracking-tight">{copy.body ?? copy.headline}</p>
      </div>
    </div>
  )
}
