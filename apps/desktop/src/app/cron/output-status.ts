import { currentI18nLocale } from '@/i18n/runtime'

/**
 * 一次 cron 运行在界面上的那一行 — `WP-CRON-OUTPUT-VISIBLE` 的桌面侧。
 *
 * 状态**不在这里判**：`cron/jobs.py` 的 `derive_output_status` 已经从调度器自己写进
 * `.md` 的那几个标记（`**Status:** script failed` / `silent (...)` / 没有 Status 行）派生出来了，
 * 这一档只把它翻成颜色与一句话（裁定 94：一份读法，多处呈现）。之前桌面上只有时刻，
 * 20:00 那句 `no notifier configured` 就藏在时刻后面没人看得见。
 */

export type CronOutputStatus = 'success' | 'silent' | 'warning' | 'failed' | 'unknown'

export type CronOutputTone = 'good' | 'muted' | 'warn' | 'bad'

/** 四语：这张状态行在日文与繁中界面上也不该露英文。 */
export type CronOutputLanguage = 'en' | 'ja' | 'zh' | 'zh-hant'

export type CronOutputEntry = {
  status?: string
  body?: string
  name?: string
  mtime?: number
  truncated?: boolean
  chars_total?: number
}

export const OUTPUT_STATUS_TEXT: Record<CronOutputStatus, Record<CronOutputLanguage, string>> = {
  success: { en: 'ok', ja: '成功', zh: '成功', 'zh-hant': '成功' },
  silent: { en: 'silent (by design)', ja: '無音（設計上そうなる）', zh: '静默（按设计不出声）', 'zh-hant': '靜默（依設計不出聲）' },
  warning: { en: 'warning', ja: '警告', zh: '有警告', 'zh-hant': '有警告' },
  failed: { en: 'failed', ja: '失敗', zh: '失败', 'zh-hant': '失敗' },
  unknown: { en: 'not read', ja: '未読', zh: '没读过', 'zh-hant': '沒讀過' }
}

const TONES: Record<CronOutputStatus, CronOutputTone> = {
  success: 'good',
  silent: 'muted',
  warning: 'warn',
  failed: 'bad',
  unknown: 'bad'
}

/** 认不出的状态一律 `bad`：界面不许比服务端先乐观。 */
export function normalizeCronOutputStatus(value: unknown): CronOutputStatus {
  const text = String(value ?? '')

  return text === 'success' || text === 'silent' || text === 'warning' || text === 'failed' ? text : 'unknown'
}

/**
 * 界面语言的真身是运行时 locale（`currentI18nLocale()`）。调用方（`cron/index.tsx`）
 * 只能表达「中/英」两种——它那一档在别人的在派格上，本刀不碰——所以它给的提示
 * 只在它说得清的时候采纳；界面是日文或繁中时，只有运行时 locale 说得清。
 */
function resolveOutputLanguage(hint?: CronOutputLanguage): CronOutputLanguage {
  const runtime = currentI18nLocale()

  if (runtime === 'ja' || runtime === 'zh-hant') {
    return runtime
  }

  if (hint === 'zh') {
    return 'zh'
  }

  return runtime === 'zh' ? 'zh' : 'en'
}

export function cronOutputView(entry: CronOutputEntry | null | undefined, language?: CronOutputLanguage) {
  const status = normalizeCronOutputStatus(entry?.status)

  return {
    status,
    tone: TONES[status],
    label: OUTPUT_STATUS_TEXT[status][resolveOutputLanguage(language)],
    body: typeof entry?.body === 'string' ? entry.body : '',
    file: typeof entry?.name === 'string' ? entry.name : '',
    truncated: entry?.truncated === true,
    charsTotal: Number.isFinite(entry?.chars_total) ? (entry?.chars_total as number) : 0
  }
}
