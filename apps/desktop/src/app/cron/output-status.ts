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

export type CronOutputEntry = {
  status?: string
  body?: string
  name?: string
  mtime?: number
  truncated?: boolean
  chars_total?: number
}

export const OUTPUT_STATUS_TEXT: Record<CronOutputStatus, { zh: string; en: string }> = {
  success: { zh: '成功', en: 'ok' },
  silent: { zh: '静默（按设计不出声）', en: 'silent (by design)' },
  warning: { zh: '有警告', en: 'warning' },
  failed: { zh: '失败', en: 'failed' },
  unknown: { zh: '没读过', en: 'not read' }
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

export function cronOutputView(entry: CronOutputEntry | null | undefined, language: 'zh' | 'en') {
  const status = normalizeCronOutputStatus(entry?.status)

  return {
    status,
    tone: TONES[status],
    label: OUTPUT_STATUS_TEXT[status][language],
    body: typeof entry?.body === 'string' ? entry.body : '',
    file: typeof entry?.name === 'string' ? entry.name : '',
    truncated: entry?.truncated === true,
    charsTotal: Number.isFinite(entry?.chars_total) ? (entry?.chars_total as number) : 0
  }
}
