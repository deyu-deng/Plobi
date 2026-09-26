import { type ToolTitleKey, translateNow } from '@/i18n'
import { normalizeExternalUrl } from '@/lib/external-link'
import { summarizeShellCommand } from '@/lib/summarize-command'
import { capitalize, normalize } from '@/lib/text'
import { extractToolErrorMessage, formatToolResultSummary } from '@/lib/tool-result-summary'

import {
  compactPreview,
  contextValue,
  formatDurationSeconds,
  isRecord,
  numberValue,
  parseMaybeObject,
  prettyJson,
  unwrapToolPayload
} from './format'
import { findFirstUrl, hostnameOf, looksLikePath, looksLikeUrl } from './targets'
import type {
  CountMetric,
  MessageRunningStateSlice,
  SearchResultRow,
  ToolMeta,
  ToolMetaSpec,
  ToolPart,
  ToolStatus,
  ToolTitleAction,
  ToolTone,
  ToolView
} from './types'

export * from './format'
export * from './targets'
export * from './types'

const FILE_EDIT_TOOL_NAMES = new Set(['edit_file', 'patch', 'write_file'])

export function isFileEditTool(toolName: string): boolean {
  return FILE_EDIT_TOOL_NAMES.has(toolName)
}

/** L1 silent-dispatch tool (MVP §8.2 C3). Keep this list tight — kanban tools stay generic. */
export function isSecretaryDispatchTool(name: string): boolean {
  return name === 'plobi_secretary_ask' || name === 'secretary_ask'
}

/**
 * WP-UI-NO-INTERNALS: a failed tool call reads as ONE short line.
 *
 * Internal material (`curl … 5030/health`, `invalid name`, `Skipped`, …) belongs
 * in the tooltip and the expanded row — not permanently parked in the middle
 * column. Success rows (including the §8.2 dispatch C3 card) pass through
 * unchanged, and the raw detail is still reachable by expanding the row.
 */
export function failureRowLabel(
  status: ToolStatus,
  view: Pick<ToolView, 'subtitle' | 'title'>,
  failedLabel: string
): { subtitle: string; title: string; tooltip: string } {
  if (status !== 'error') {
    return { subtitle: view.subtitle, title: view.title, tooltip: '' }
  }

  const tooltip = [view.title, view.subtitle]
    .map(part => (part || '').trim())
    .filter(Boolean)
    .join(' · ')

  return { subtitle: '', title: failedLabel, tooltip }
}

export interface DiffLineStats {
  added: number
  removed: number
}

export function countDiffLineStats(diff: string): DiffLineStats {
  let added = 0
  let removed = 0

  for (const line of diff.split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) {
      added += 1
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      removed += 1
    }
  }

  return { added, removed }
}

function fileEditPath(args: Record<string, unknown>, result: Record<string, unknown>): string {
  return (
    firstStringField(args, ['path', 'file', 'filepath']) ||
    firstStringField(result, ['path', 'file', 'filepath', 'resolved_path']) ||
    htmlPathFromInlineDiff(firstStringField(result, ['inline_diff', 'diff']))
  )
}

function fileEditBasename(path: string): string {
  const normalized = path.replace(/\\/g, '/').trim()

  return normalized.split('/').filter(Boolean).pop() || normalized
}

function numericField(record: Record<string, unknown>, key: string): number | undefined {
  const value = record[key]

  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function readFileLineLabel(args: Record<string, unknown>, result: Record<string, unknown>): string {
  if (numericField(args, 'offset') === undefined && numericField(args, 'limit') === undefined) {
    return ''
  }

  const content = firstStringField(result, ['content'])
  const offset = numericField(args, 'offset')
  const limit = numericField(args, 'limit')

  if (offset !== undefined && offset > 0) {
    if (limit === undefined || limit <= 1) {
      return `L${offset}`
    }

    return `L${offset}-${offset + limit - 1}`
  }

  const lines = content
    .split('\n')
    .map(line => /^(\d+)\|/.exec(line)?.[1])
    .filter((line): line is string => !!line)
    .map(Number)

  if (lines.length === 0) {
    return ''
  }

  const start = lines[0]!
  const end = lines[lines.length - 1]!

  return start === end ? `L${start}` : `L${start}-${end}`
}

function readFileDisplayTarget(args: Record<string, unknown>, result: Record<string, unknown>): string {
  const inherited = firstStringField(args, ['context', 'preview'])

  if (inherited) {
    return inherited
  }

  const path = firstStringField(args, ['path', 'file', 'filepath'])

  if (!path) {
    return ''
  }

  const lineLabel = readFileLineLabel(args, result)

  return [fileEditBasename(path), lineLabel].filter(Boolean).join(' ')
}

const TOOL_META: Record<ToolTitleKey, ToolMetaSpec> = {
  browser_click: {
    icon: 'globe',
    tone: 'browser'
  },
  browser_fill: {
    icon: 'globe',
    tone: 'browser'
  },
  browser_navigate: {
    icon: 'globe',
    tone: 'browser'
  },
  browser_snapshot: {
    icon: 'globe',
    tone: 'browser'
  },
  browser_take_screenshot: {
    icon: 'file-media',
    tone: 'browser'
  },
  browser_type: {
    icon: 'globe',
    tone: 'browser'
  },
  clarify: {
    icon: 'question',
    tone: 'agent'
  },
  cronjob: {
    icon: 'watch',
    tone: 'agent'
  },
  edit_file: { icon: 'edit', tone: 'file' },
  execute_code: {
    icon: 'terminal',
    tone: 'terminal'
  },
  image_generate: {
    icon: 'file-media',
    tone: 'image'
  },
  list_files: {
    icon: 'files',
    tone: 'file'
  },
  patch: { icon: 'edit', tone: 'file' },
  read_file: { icon: 'file', tone: 'file' },
  search_files: {
    icon: 'search',
    tone: 'file'
  },
  session_search_recall: {
    icon: 'search',
    tone: 'agent'
  },
  terminal: {
    icon: 'terminal',
    tone: 'terminal'
  },
  todo: { icon: 'tools', tone: 'agent' },
  vision_analyze: {
    icon: 'eye',
    tone: 'image'
  },
  web_extract: { icon: 'globe', tone: 'web' },
  web_search: { icon: 'search', tone: 'web' },
  write_file: { icon: 'edit', tone: 'file' }
}

function isToolTitleKey(name: string): name is ToolTitleKey {
  return name in TOOL_META
}

const INLINE_CODE_SPLIT_RE = /(`[^`\n]+`)/g
const CITATION_MARKER_RE = /(?<=[\p{L}\p{N})\].,!?:;"'”’])\[(?:\d+(?:\s*,\s*\d+)*)\](?!\()/gu
const BACKTICK_NOISE_RE = /`{3,}/g

export const selectMessageRunning = (state: MessageRunningStateSlice) =>
  state.thread.isRunning && state.message.status?.type === 'running'

function titleForTool(name: string): string {
  const normalized = name.replace(/^browser_/, '').replace(/^web_/, '')

  return normalized.split('_').filter(Boolean).map(capitalize).join(' ') || name
}

const PREFIX_META: { icon?: string; labelKey: string; prefix: string; tone: ToolTone }[] = [
  { prefix: 'browser_', labelKey: 'browser', icon: 'globe', tone: 'browser' },
  { prefix: 'web_', labelKey: 'web', icon: 'globe', tone: 'web' }
]

function toolMeta(name: string): ToolMeta {
  if (isToolTitleKey(name)) {
    const meta = TOOL_META[name]

    return {
      done: translateNow(`assistant.tool.titles.${name}.done`),
      pending: translateNow(`assistant.tool.titles.${name}.pending`),
      pendingAction: translateNow(`assistant.tool.titles.${name}.pendingAction`),
      icon: meta.icon,
      tone: meta.tone
    }
  }

  const action = titleForTool(name)
  const prefix = PREFIX_META.find(p => name.startsWith(p.prefix))

  if (prefix) {
    const prefixLabel = translateNow(`assistant.tool.prefixes.${prefix.labelKey}`)

    return {
      done: translateNow('assistant.tool.titleTemplates.prefixedDone', prefixLabel, action),
      pending: translateNow('assistant.tool.titleTemplates.runningPrefixedTool', prefixLabel, action),
      pendingAction: translateNow('assistant.tool.actions.running'),
      icon: prefix.icon,
      tone: prefix.tone
    }
  }

  return {
    done: action,
    pending: translateNow('assistant.tool.titleTemplates.runningTool', action),
    pendingAction: translateNow('assistant.tool.actions.running'),
    tone: 'default'
  }
}

const COUNT_FIELD_KEYS = [
  'count',
  'total',
  'result_count',
  'results_count',
  'num_results',
  'match_count',
  'matches_count',
  'file_count',
  'files_count',
  'item_count',
  'items_count',
  'search_count',
  'searches_count',
  'source_count',
  'sources_count',
  'document_count',
  'documents_count',
  'updated',
  'added',
  'removed',
  'deleted',
  'created',
  'changed',
  'processed',
  'steps'
] as const

const COUNT_ARRAY_KEYS = ['results', 'items', 'matches', 'files', 'documents', 'sources', 'rows'] as const

const COUNT_EXCLUDED_KEYS = new Set(['duration_s', 'exit_code', 'status_code'])

const COUNT_NOUN_BY_FIELD: Partial<Record<(typeof COUNT_FIELD_KEYS)[number], string>> = {
  count: '',
  total: '',
  result_count: 'result',
  results_count: 'result',
  num_results: 'result',
  match_count: 'match',
  matches_count: 'match',
  file_count: 'file',
  files_count: 'file',
  item_count: 'item',
  items_count: 'item',
  search_count: 'search',
  searches_count: 'search',
  source_count: 'source',
  sources_count: 'source',
  document_count: 'document',
  documents_count: 'document',
  updated: 'item',
  added: 'item',
  removed: 'item',
  deleted: 'item',
  created: 'item',
  changed: 'item',
  processed: 'item',
  steps: 'step'
}

const COUNT_NOUN_BY_ARRAY: Record<(typeof COUNT_ARRAY_KEYS)[number], string> = {
  documents: 'document',
  files: 'file',
  items: 'item',
  matches: 'match',
  results: 'result',
  rows: 'row',
  sources: 'source'
}

const DEFAULT_COUNT_NOUN_BY_TOOL: Record<string, string> = {
  browser_snapshot: 'item',
  list_files: 'file',
  search_files: 'result',
  session_search_recall: 'result',
  todo: 'todo',
  web_search: 'result'
}

function countFromUnknown(value: unknown): null | number {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.length : null
  }

  const n = numberValue(value)

  if (n === null || n <= 0) {
    return null
  }

  return Math.round(n)
}

function singularizeNoun(noun: string): string {
  const normalized = normalize(noun)

  if (!normalized) {
    return ''
  }

  if (normalized.endsWith('ies') && normalized.length > 3) {
    return `${normalized.slice(0, -3)}y`
  }

  if (/(xes|zes|ches|shes|sses)$/.test(normalized) && normalized.length > 3) {
    return normalized.slice(0, -2)
  }

  if (normalized.endsWith('s') && normalized.length > 2 && !normalized.endsWith('ss')) {
    return normalized.slice(0, -1)
  }

  return normalized
}

function pluralizeNoun(noun: string, count: number): string {
  if (count === 1) {
    return noun
  }

  if (noun === 'search') {
    return 'searches'
  }

  if (noun.endsWith('y') && noun.length > 1 && !/[aeiou]y$/i.test(noun)) {
    return `${noun.slice(0, -1)}ies`
  }

  if (/(s|x|z|ch|sh)$/i.test(noun)) {
    return `${noun}es`
  }

  return `${noun}s`
}

function formatCountLabel(metric: CountMetric): string {
  return `${metric.count} ${pluralizeNoun(metric.noun, metric.count)}`
}

function countMetric(count: number, noun: string): CountMetric {
  return { count, noun: singularizeNoun(noun) || 'item' }
}

function normalizeMetricForTool(toolName: string, metric: CountMetric): CountMetric {
  if (toolName === 'web_search') {
    return countMetric(metric.count, 'result')
  }

  return metric
}

function fallbackCountNoun(toolName: string): string {
  return DEFAULT_COUNT_NOUN_BY_TOOL[toolName] || 'item'
}

function dynamicCountNounFromKey(key: string, fallbackNoun: string): string {
  const normalized = key.toLowerCase()

  if (normalized === 'count' || normalized === 'total') {
    return fallbackNoun
  }

  const stripped = normalized.replace(/_(count|total)$/i, '').replace(/^num_/, '')

  return singularizeNoun(stripped) || fallbackNoun
}

function countFromRecord(record: Record<string, unknown>, fallbackNoun: string): CountMetric | null {
  for (const key of COUNT_FIELD_KEYS) {
    const value = record[key]
    const count = countFromUnknown(value)

    if (count !== null) {
      return countMetric(count, COUNT_NOUN_BY_FIELD[key] || fallbackNoun)
    }
  }

  for (const key of COUNT_ARRAY_KEYS) {
    const value = record[key]
    const count = countFromUnknown(value)

    if (count !== null) {
      return countMetric(count, COUNT_NOUN_BY_ARRAY[key] || fallbackNoun)
    }
  }

  for (const [key, value] of Object.entries(record)) {
    if (COUNT_EXCLUDED_KEYS.has(key)) {
      continue
    }

    if (!/_count$|_total$/i.test(key)) {
      continue
    }

    const count = countFromUnknown(value)

    if (count !== null) {
      return countMetric(count, dynamicCountNounFromKey(key, fallbackNoun))
    }
  }

  return null
}

function countFromText(value: string, fallbackNoun: string): CountMetric | null {
  const text = value.trim()

  if (!text) {
    return null
  }

  const unitMatch =
    text.match(/\b(\d+)\s+(results?|items?|files?|matches?|documents?|sources?|searches?|steps?|rows?)\b/i) ||
    text.match(/\b(?:did|found|returned|listed|searched|matched|updated|created|deleted|processed)\s+(\d+)\b/i)

  if (unitMatch?.[1]) {
    const n = Number(unitMatch[1])
    const noun = unitMatch[2] ? singularizeNoun(unitMatch[2]) : fallbackNoun

    return Number.isFinite(n) && n > 0 ? countMetric(Math.round(n), noun) : null
  }

  return null
}

function toolResultCount(
  part: ToolPart,
  argsRecord: Record<string, unknown>,
  resultRecord: Record<string, unknown>
): CountMetric | null {
  if (part.result === undefined) {
    return null
  }

  const fallbackNounByTool = fallbackCountNoun(part.toolName)

  if (part.toolName === 'web_search') {
    const hits = collectResultItems(part.result)

    if (hits.length) {
      return countMetric(hits.length, 'result')
    }
  }

  const directCount = countFromRecord(resultRecord, fallbackNounByTool)

  if (directCount !== null) {
    return normalizeMetricForTool(part.toolName, directCount)
  }

  const payload = unwrapToolPayload(part.result)

  if (isRecord(payload)) {
    const payloadCount = countFromRecord(payload, fallbackNounByTool)

    if (payloadCount !== null) {
      return normalizeMetricForTool(part.toolName, payloadCount)
    }
  }

  const summaryText =
    firstStringField(resultRecord, ['summary', 'message', 'detail']) || fallbackDetailText(argsRecord, resultRecord)

  const textMetric = countFromText(summaryText, fallbackNounByTool)

  return textMetric ? normalizeMetricForTool(part.toolName, textMetric) : null
}

export function looksRedundant(title: string, detail: string): boolean {
  if (!detail) {
    return true
  }

  const norm = (input: string) => input.toLowerCase().replace(/\s+/g, ' ').trim()

  return norm(title) === norm(detail)
}

export function cleanVisibleText(text: string): string {
  return text
    .split(INLINE_CODE_SPLIT_RE)
    .map(part =>
      part.startsWith('`')
        ? part
        : part
            .replace(BACKTICK_NOISE_RE, '')
            .replace(CITATION_MARKER_RE, '')
            .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label: string, href: string) => {
              const normalized = normalizeExternalUrl(href)

              return `${label} ${normalized}`
            })
    )
    .join('')
}

function summarizeBrowserSnapshot(snapshot: string): string {
  const count = (re: RegExp) => snapshot.match(re)?.length ?? 0

  const stats = [
    `${count(/button\s+"[^"]+"/g)} buttons`,
    `${count(/link\s+"[^"]+"/g)} links`,
    `${count(/(?:textbox|combobox|searchbox)\s+"[^"]+"/g)} inputs`
  ].join(' · ')

  const labels = Array.from(snapshot.matchAll(/(?:button|link|combobox|textbox)\s+"([^"]+)"/g))
    .map(m => m[1].trim())
    .filter(Boolean)
    .slice(0, 4)

  return labels.length ? `${stats}\nTop controls: ${labels.join(', ')}` : stats
}

function firstStringField(record: Record<string, unknown>, keys: readonly string[]): string {
  for (const key of keys) {
    const value = record[key]

    if (typeof value === 'string' && value.trim()) {
      return value.trim()
    }
  }

  return ''
}

/** Known L2 short labels (裁定 13) — mirror `plobi/console/router.py` `_AGENT_SHORT_NAMES`. */
const SECRETARY_AGENT_SHORT_NAMES: Record<string, string> = {
  agenda: '日程秘书',
  'agenda-secretary': '日程秘书',
  'secretary-agenda': '日程秘书'
}

function secretaryField(
  args: Record<string, unknown>,
  result: Record<string, unknown>,
  keys: readonly string[]
): string {
  return firstStringField(result, keys) || firstStringField(args, keys)
}

function secretaryAgentRecord(result: Record<string, unknown>): Record<string, unknown> {
  const nested = result.agent

  return isRecord(nested) ? nested : {}
}

function secretaryAgendaRecord(result: Record<string, unknown>): Record<string, unknown> {
  const nested = result.agenda

  return isRecord(nested) ? nested : {}
}

function secretaryAgendaEvents(result: Record<string, unknown>): unknown[] {
  const agenda = secretaryAgendaRecord(result)
  const events = agenda.events

  if (Array.isArray(events)) {
    return events
  }

  const today = Array.isArray(agenda.today_events) ? agenda.today_events : []
  const tomorrow = Array.isArray(agenda.tomorrow_events) ? agenda.tomorrow_events : []

  return [...today, ...tomorrow]
}

function formatSecretaryEventLines(events: unknown[], limit = 12): string {
  return events
    .slice(0, limit)
    .map(item => {
      const row = isRecord(item) ? item : {}
      const title = firstStringField(row, ['title', 'name', 'summary']) || 'event'
      const when = firstStringField(row, ['start_at', 'when', 'at'])

      return when ? `- ${title} · ${when}` : `- ${title}`
    })
    .join('\n')
}

/**
 * C3 "派给谁": prefer real `agent.id` (+ 裁定 13 short map). Do not rely on the
 * WP-G1 test-only `agent_name` field; fall back to it only as a last resort.
 */
function secretaryTarget(args: Record<string, unknown>, result: Record<string, unknown>): string {
  const agent = secretaryAgentRecord(result)
  const id = firstStringField(agent, ['id', 'name']) || firstStringField(result, ['agent_id'])
  const key = id.trim().toLowerCase()

  if (key && SECRETARY_AGENT_SHORT_NAMES[key]) {
    return SECRETARY_AGENT_SHORT_NAMES[key]!
  }

  if (id) {
    return id
  }

  const legacy =
    firstStringField(result, ['agent_name', 'secretary', 'target', 'name']) ||
    firstStringField(args, ['agent_name', 'secretary', 'target', 'name'])

  if (legacy && !isRecord(parseMaybeObject(legacy))) {
    return legacy
  }

  // This tool only routes to the agenda L2 (S2) — waiting state has no agent yet.
  return '日程秘书'
}

function secretaryIntentLabel(intent: string): string {
  if (intent === 'refresh_agenda') {
    return 'refresh agenda'
  }

  if (intent === 'write_briefing') {
    return 'write briefing'
  }

  if (intent === 'query_agenda') {
    return 'query agenda'
  }

  if (intent === 'decide_pending') {
    return 'decide pending'
  }

  if (intent === 'project_status') {
    return 'project status'
  }

  if (intent === 'plan_day') {
    return 'plan day'
  }

  return intent.replace(/_/g, ' ')
}

/** Naive local ISO → `HH:MM` ('' when unparseable). */
function mutateClock(iso: string): string {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : ''
}

function mutateEventRecord(result: Record<string, unknown>): Record<string, unknown> {
  const event = result.event

  return isRecord(event) ? event : {}
}

function mutateEventTitle(result: Record<string, unknown>, args: Record<string, unknown>): string {
  const event = mutateEventRecord(result)

  return (
    firstStringField(event, ['title']) ||
    firstStringField(result, ['title']) ||
    firstStringField(args, ['title']) ||
    ''
  )
}

/**
 * 裁定 27: a spoken add/update/cancel is DONE work, not a dispatch. The card
 * must read 已记下 / 已改 / 已取消 — never "Asked 日程秘书", and never the
 * `agenda_propose` pending-confirmation card (a spoken order is a human order).
 * Failures fall through to the generic `failureRowLabel` one-liner.
 */
function mutateHeadline(
  result: Record<string, unknown>,
  args: Record<string, unknown>,
  part: ToolPart
): ToolTitleParts {
  if (part.result === undefined || result.ok === false) {
    // For the pending / failure case, the bulk cancel shows its own
    // 「取消课表中」 instead of the single-event 「记日程中」 — they're
    // different surface moods and shouldn't share a phrase.
    const pendingAction = firstStringField(args, ['action'])
    if (pendingAction === 'cancel_matching') {
      return { title: translateNow('assistant.tool.mutate.cancelMatching.pending') }
    }

    return { title: translateNow('assistant.tool.mutate.pending') }
  }

  const action = firstStringField(result, ['action']) || firstStringField(args, ['action']) || 'create'

  // 裁定 33.5: cancel_matching is a bulk cancel of future classes — its
  // surface looks nothing like add/update/delete and must not reuse
  // 已记下 / 已改 / 已取消 单条文案.
  if (action === 'cancel_matching') {
    return cancelMatchingHeadline(result, part)
  }

  const key = action === 'update' || action === 'delete' ? action : 'create'

  return { title: translateNow(`assistant.tool.mutate.${key}`) }
}

/** 标题 + `15:00–16:00`，或 `15:00 · 未写结束`（复用看板那套空结束文案）。 */
function mutateSubtitleLine(result: Record<string, unknown>, args: Record<string, unknown>): string {
  const event = mutateEventRecord(result)
  const title = mutateEventTitle(result, args)
  const start = mutateClock(firstStringField(event, ['start_at']) || '')
  const end = mutateClock(firstStringField(event, ['end_at']) || '')
  const span = !start ? '' : end ? `${start}–${end}` : `${start} · ${translateNow('agenda.noEnd')}`

  return [title, span].filter(Boolean).join(' · ')
}

/* -------------------------------------------------------------------------- */
/* cancel_matching card (WP-CANCEL-CARD, 裁定 33.5)                            */
/*                                                                            */
/* `mutate_agenda` + `action=cancel_matching` is a bulk cancel that takes the  */
/* "future timetable from this date" away. The single-line summary model      */
/* (added / updated / cancelled) fits one event — not a series — so this card  */
/* reuses the backend's own `cancelled` count and `sample` titles. N=0 is a   */
/* real success (nothing to cancel), not a failure.                            */
/* -------------------------------------------------------------------------- */

const CANCEL_MATCHING_PREVIEW_ROWS = 3

function cancelMatchingCount(result: Record<string, unknown>): number {
  const value = result.cancelled
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  // The backend may serialise the count as a string — accept it only when well-formed.
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) {
      return parsed
    }
  }

  return 0
}

function cancelMatchingFromDate(result: Record<string, unknown>): string {
  return firstStringField(result, ['from_date']) || ''
}

function cancelMatchingHeadline(result: Record<string, unknown>, _part: ToolPart): ToolTitleParts {
  const count = cancelMatchingCount(result)

  return { title: translateNow('assistant.tool.mutate.cancelMatching.title', count) }
}

function cancelMatchingSubtitleLine(result: Record<string, unknown>): string {
  const count = cancelMatchingCount(result)
  const fromDate = cancelMatchingFromDate(result)
  const sample = agendaRows(result.sample).slice(0, CANCEL_MATCHING_PREVIEW_ROWS)
  const sampleOverflow = agendaRows(result.sample).length > CANCEL_MATCHING_PREVIEW_ROWS
    ? translateNow('assistant.tool.mutate.cancelMatching.more', agendaRows(result.sample).length - CANCEL_MATCHING_PREVIEW_ROWS)
    : ''

  if (count === 0) {
    // N=0 is still a successful cancel — never pretend it's a failure.
    return translateNow('assistant.tool.mutate.cancelMatching.empty')
  }

  const head = sample
    .map(row => agendaRowTitle(row))
    .filter(Boolean)
    .join(' / ')
  const fromPart = fromDate ? translateNow('assistant.tool.mutate.cancelMatching.fromDate', fromDate) : ''
  const headAndOverflow = sampleOverflow ? `${head} ${sampleOverflow}` : head

  return [fromPart, headAndOverflow].filter(Boolean).join(' · ')
}

/* -------------------------------------------------------------------------- */
/* query_agenda / decide_pending cards (WP-SEC-VOCAB, 裁定 28.4)               */
/*                                                                            */
/* The backend answers these from the shared agenda store — the card just     */
/* reads back what it returned. The frontend never works out "today", never   */
/* filters pending itself, and never invents a clock.                          */
/* -------------------------------------------------------------------------- */

/** Rows the backend handed us (`result.events` / `result.pending` / `candidates`). */
function agendaRows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function agendaRowTitle(row: Record<string, unknown>): string {
  return firstStringField(row, ['title', 'name', 'summary']) || ''
}

function agendaClock(iso: string): string {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : ''
}

/** `HH:MM 标题` / `HH:MM–HH:MM 标题` — no end → start only (省字数，不写「未写结束」). */
function agendaRowLine(row: Record<string, unknown>): string {
  const start = agendaClock(firstStringField(row, ['start_at']) || '')
  const end = agendaClock(firstStringField(row, ['end_at']) || '')
  const when = !start ? '' : end ? `${start}–${end}` : start

  return [when, agendaRowTitle(row)].filter(Boolean).join(' ')
}

/** How many rows the subtitle lists before switching to `+K`. */
const QUERY_PREVIEW_ROWS = 3

function queryRangeOf(result: Record<string, unknown>, args: Record<string, unknown>): string {
  return (
    firstStringField(result, ['range']) ||
    firstStringField(args, ['range']) ||
    (agendaRows(result.pending).length && !agendaRows(result.events).length ? 'pending' : 'today')
  )
}

/**
 * 裁定 28.4: a spoken read-back is an answer, not a dispatch — the title says
 * which slice of the agenda was read and how many entries it holds. `N` is
 * always the backend's own array length (pending range counts `pending`).
 */
function queryHeadline(
  result: Record<string, unknown>,
  args: Record<string, unknown>,
  part: ToolPart
): ToolTitleParts {
  if (part.result === undefined) {
    return { title: translateNow('assistant.tool.query.viewing') }
  }

  if (result.ok === false) {
    return { title: translateNow('assistant.tool.query.viewing') }
  }

  const range = queryRangeOf(result, args)
  const events = agendaRows(result.events)
  const pending = agendaRows(result.pending)
  const count = range === 'pending' ? pending.length : events.length

  if (range === 'tomorrow') {
    return { title: translateNow('assistant.tool.query.tomorrow', count) }
  }

  if (range === 'week') {
    return { title: translateNow('assistant.tool.query.week', count) }
  }

  if (range === 'date') {
    // `MM-DD` comes from the backend's own `from` — never derived client-side.
    const from = firstStringField(result, ['from']) || firstStringField(args, ['date']) || ''
    const short = from.length >= 10 ? from.slice(5, 10) : from

    return short
      ? { title: translateNow('assistant.tool.query.date', short, count) }
      : { title: translateNow('assistant.tool.query.today', count) }
  }

  if (range === 'pending') {
    return { title: translateNow('assistant.tool.query.pending', count) }
  }

  return { title: translateNow('assistant.tool.query.today', count) }
}

/** 前 3 条 `HH:MM 标题`（+K），尾部带 `· 待确认 M`；两列都空 → 没有安排。
 *  后端给 `anchors` 时追加作息前 2 条；`anchors` 缺席 → 旧卡（WP-SHELL-CALM，裁定 34.2）。 */
const ANCHOR_PREVIEW_ROWS = 2

function agendaAnchorLine(row: Record<string, unknown>): string {
  const start = agendaClock(firstStringField(row, ['start_at']) || '')
  const end = agendaClock(firstStringField(row, ['end_at']) || '')
  const when = !start ? '' : end ? `${start}–${end}` : start

  return [when, agendaRowTitle(row)].filter(Boolean).join(' ')
}

function querySubtitleLine(result: Record<string, unknown>, args: Record<string, unknown>): string {
  if (result.ok === false) {
    return ''
  }

  const range = queryRangeOf(result, args)
  const events = agendaRows(result.events)
  const pending = agendaRows(result.pending)
  const rows = range === 'pending' ? pending : events
  const lines = rows.slice(0, QUERY_PREVIEW_ROWS).map(agendaRowLine).filter(Boolean)
  const overflow =
    rows.length > QUERY_PREVIEW_ROWS ? translateNow('assistant.tool.query.more', rows.length - QUERY_PREVIEW_ROWS) : ''
  const body = lines.length
    ? [lines.join(' / '), overflow].filter(Boolean).join(' ')
    : translateNow('assistant.tool.query.empty')
  const pendingTail =
    range !== 'pending' && pending.length
      ? translateNow('assistant.tool.query.pendingTail', pending.length)
      : ''

  // 裁定 34.2: anchors = the day's routine anchors (meals, sleep...). The
  // card surfaces them after the events so a user asking 「今天有什么」 can
  // also see when lunch is. Anchors are NOT part of N — they don't inflate
  // the event count in the headline (裁定 28.4 keeps N = events.length).
  const anchors = agendaRows(result.anchors).slice(0, ANCHOR_PREVIEW_ROWS)
  const anchorLine =
    anchors.length > 0 ? anchors.map(agendaAnchorLine).filter(Boolean).join(' / ') : ''

  return [body, anchorLine, pendingTail].filter(Boolean).join(' · ')
}

/**
 * 裁定 28.4: spoken confirm / dismiss of a pending entry. Success reads
 * 「已确认 · 组会 10:00」/「已忽略 · 组会 10:00」; a dismissed pending that had no
 * previous value is removed from the store, so the card says 已忽略并移除.
 */
function decideHeadline(
  result: Record<string, unknown>,
  args: Record<string, unknown>,
  part: ToolPart
): ToolTitleParts {
  if (part.result === undefined || result.ok === false) {
    return { title: translateNow('assistant.tool.decide.pending') }
  }

  const decision = firstStringField(result, ['decision']) || firstStringField(args, ['decision']) || 'confirm'
  const verb =
    decision === 'dismiss'
      ? result.deleted === true
        ? translateNow('assistant.tool.decide.removed')
        : translateNow('assistant.tool.decide.dismissed')
      : translateNow('assistant.tool.decide.confirmed')
  const event = isRecord(result.event) ? result.event : {}
  const target = [agendaRowTitle(event), agendaClock(firstStringField(event, ['start_at']) || '')]
    .filter(Boolean)
    .join(' ')

  return { title: [verb, target].filter(Boolean).join(' · ') }
}

/** `candidates`（多条匹配）进可展开体；没有候选就不展开。 */
function secretaryCandidateLines(result: Record<string, unknown>): string {
  return agendaRows(result.candidates)
    .map(row => {
      const start = firstStringField(row, ['start_at']) || ''

      return `- ${[agendaRowTitle(row), start].filter(Boolean).join(' · ')}`
    })
    .join('\n')
}

/* -------------------------------------------------------------------------- */
/* project_status card (WP-PROJECT-CARD, 裁定 30.1)                            */
/*                                                                            */
/* Spoken "projects status" is a read-back, not a dispatch — same posture as   */
/* query_agenda. The card prints the backend's own `name`s + `weekly_hours`;  */
/* the frontend never invents numbers, never sums across the list.            */
/* -------------------------------------------------------------------------- */

const PROJECT_PREVIEW_ROWS = 3

function projectRows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function projectWeeklyHours(row: Record<string, unknown>): null | number {
  const hours = row.weekly_hours
  if (typeof hours === 'number' && Number.isFinite(hours)) {
    return hours
  }
  // Some backends serialise numbers as strings — accept only when they're well-formed.
  if (typeof hours === 'string' && hours.trim() !== '') {
    const parsed = Number(hours)
    if (Number.isFinite(parsed)) {
      return parsed
    }
  }

  return null
}

/** `项目名 · 每周 Nh` / `项目名 · 未设节奏` — never a synthesized cadence. */
function projectRowLine(row: Record<string, unknown>): string {
  const name = firstStringField(row, ['name']) || firstStringField(row, ['id']) || ''
  const hours = projectWeeklyHours(row)
  const cadence = hours === null ? translateNow('assistant.tool.project.noRhythm') : translateNow('assistant.tool.project.hours', hours)

  return cadence ? `${name} · ${cadence}` : name
}

/**
 * 裁定 30.1: spoken read-back is an answer, not a dispatch — the title says
 * `各项目 · N` (N is the backend's own array length) and the subtitle lists
 * the first three rows with their cadence.
 */
function projectHeadline(
  result: Record<string, unknown>,
  part: ToolPart
): ToolTitleParts {
  if (part.result === undefined || result.ok === false) {
    return { title: translateNow('assistant.tool.project.viewing') }
  }

  return { title: translateNow('assistant.tool.project.title', projectRows(result.projects).length) }
}

function projectSubtitleLine(result: Record<string, unknown>): string {
  if (result.ok === false) {
    return ''
  }

  const rows = projectRows(result.projects)

  if (rows.length === 0) {
    return translateNow('assistant.tool.project.empty')
  }

  const head = rows.slice(0, PROJECT_PREVIEW_ROWS).map(projectRowLine).filter(Boolean)
  const overflow =
    rows.length > PROJECT_PREVIEW_ROWS ? translateNow('assistant.tool.query.more', rows.length - PROJECT_PREVIEW_ROWS) : ''

  return [head.join(' / '), overflow].filter(Boolean).join(' ')
}

/* -------------------------------------------------------------------------- */
/* plan_day card (WP-PLAN-DAY-CARD, 裁定 32.5)                                */
/*                                                                            */
/* Spoken "plan my day" is a write answer, not a dispatch — the card reads    */
/* `已排出 · YYYY-MM-DD · N 项` with conflict count surfaced, and the front   */
/* 3 rows show times+title. The frontend never invents a clock and never      */
/* invents the date — both come from the backend's `for_date` and `items`.    */
/* -------------------------------------------------------------------------- */

const PLAN_PREVIEW_ROWS = 3

function planDayDateOf(result: Record<string, unknown>, args: Record<string, unknown>): string {
  return (
    firstStringField(result, ['for_date']) ||
    firstStringField(args, ['for_date']) ||
    firstStringField(args, ['date']) ||
    ''
  )
}

function planDayConflictCount(result: Record<string, unknown>): number {
  const count = result.conflict_count
  if (typeof count === 'number' && Number.isFinite(count)) {
    return count
  }

  return 0
}

/** `HH:MM–HH:MM 标题` / `HH:MM 标题` — open-ended prints only its start. */
function planDayRowLine(row: Record<string, unknown>): string {
  const start = agendaClock(firstStringField(row, ['start_at']) || '')
  const end = agendaClock(firstStringField(row, ['end_at']) || '')
  const when = !start ? '' : end ? `${start}–${end}` : start

  return [when, agendaRowTitle(row)].filter(Boolean).join(' ')
}

function planDayHeadline(
  result: Record<string, unknown>,
  args: Record<string, unknown>,
  part: ToolPart
): ToolTitleParts {
  if (part.result === undefined || result.ok === false) {
    return { title: translateNow('assistant.tool.planDay.pending') }
  }

  const date = planDayDateOf(result, args)
  const items = agendaRows(result.items).length

  // Date comes from the backend's `for_date`; the frontend never derives
  // "today" on its own — no client-side date math here on purpose.
  return date
    ? { title: translateNow('assistant.tool.planDay.title', date, items) }
    : { title: translateNow('assistant.tool.planDay.pending') }
}

function planDaySubtitleLine(result: Record<string, unknown>): string {
  if (result.ok === false) {
    return ''
  }

  const items = agendaRows(result.items)
  const summary = firstStringField(result, ['summary'])
  const conflicts = planDayConflictCount(result)

  if (items.length === 0) {
    return translateNow('assistant.tool.planDay.empty')
  }

  const head = items.slice(0, PLAN_PREVIEW_ROWS).map(planDayRowLine).filter(Boolean)
  const overflow = items.length > PLAN_PREVIEW_ROWS ? translateNow('assistant.tool.query.more', items.length - PLAN_PREVIEW_ROWS) : ''
  const headJoined = head.join(' / ')
  const summaryLine = summary ? summary.split(/[。.!?]/, 1)[0]?.trim() : ''
  const summaryPart = summaryLine || ''
  const conflictPart = conflicts > 0 ? translateNow('assistant.tool.planDay.conflicts', conflicts) : ''

  // Same ` +K` convention as `querySubtitleLine` — no ` · ` before the overflow token.
  const headAndOverflow = overflow ? `${headJoined} ${overflow}` : headJoined

  return [headAndOverflow, summaryPart, conflictPart].filter(Boolean).join(' · ')
}

/* -------------------------------------------------------------------------- */
/* checkin proposal card (WP-STUDIO, 裁定 36.3)                                */
/*                                                                            */
/* `plobi_checkin_respond` turns "别中午排会" into a proposal card the user   */
/* confirms with the existing 「确认 N」 path — it never writes config itself.  */
/* The card must read like a card you can repeat back, not a nameless tool row.*/
/* -------------------------------------------------------------------------- */

const CHECKIN_PREVIEW_ROWS = 2

function isCheckinTool(toolName: string): boolean {
  return toolName === 'plobi_checkin_respond' || toolName === 'checkin_respond'
}

/** `questions` entries are `{key,text,evidence}`; tolerate a bare string too. */
function checkinQuestionText(entry: unknown): string {
  if (typeof entry === 'string') {
    return entry.trim()
  }

  if (isRecord(entry)) {
    return firstStringField(entry, ['text', 'question', 'label']) || ''
  }

  return ''
}

function checkinConfirmSeq(result: Record<string, unknown>): null | number {
  const value = result.confirm_seq
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) {
      return parsed
    }
  }

  return null
}

function checkinHeadline(result: Record<string, unknown>, part: ToolPart): ToolTitleParts {
  if (part.result === undefined || result.ok === false) {
    return { title: translateNow('assistant.tool.checkin.pending') }
  }

  const seq = checkinConfirmSeq(result)

  // Free-text-only responses archive a note and build no card — nothing to
  // confirm, so we say so instead of inventing a 「确认 0」.
  return seq === null
    ? { title: translateNow('assistant.tool.checkin.empty') }
    : { title: translateNow('assistant.tool.checkin.title', seq) }
}

function checkinSubtitleLine(result: Record<string, unknown>): string {
  if (result.ok === false || checkinConfirmSeq(result) === null) {
    return ''
  }

  const questions = Array.isArray(result.questions) ? result.questions : []

  // The questions are already human wording from the backend — print them
  // as-is (no rewriting, no summarizing).
  return questions
    .slice(0, CHECKIN_PREVIEW_ROWS)
    .map(checkinQuestionText)
    .filter(Boolean)
    .join(' / ')
}

function secretaryRouteLabel(route: string): string {
  if (!route) {
    return ''
  }

  return `route=${route}`
}

function secretaryDeadDoor(result: Record<string, unknown>): string {
  if (result.dead === true || result.dead === 'true') {
    return firstStringField(result, ['error', 'message', 'detail']) || 'chatlog is not healthy'
  }

  return ''
}

function collectResultItems(value: unknown): unknown[] {
  if (Array.isArray(value)) {
    return value
  }

  const record = parseMaybeObject(value)

  for (const key of [
    'web',
    'results',
    'search_results',
    'sources',
    'web_sources',
    'items',
    'organic_results',
    'organic',
    'matches',
    'documents'
  ]) {
    const candidate = record[key]

    if (Array.isArray(candidate)) {
      return candidate
    }

    if (isRecord(candidate)) {
      const nested = collectResultItems(candidate)

      if (nested.length) {
        return nested
      }
    }
  }

  const payload = unwrapToolPayload(record)

  return payload === record ? [] : collectResultItems(payload)
}

function extractSearchResults(result: unknown, limit = 6): SearchResultRow[] {
  const list = collectResultItems(result)

  return list
    .map(item => {
      const r = parseMaybeObject(item)

      return {
        title: cleanVisibleText(firstStringField(r, ['title', 'name'])),
        url: firstStringField(r, ['url', 'href', 'link']),
        snippet: cleanVisibleText(firstStringField(r, ['snippet', 'description', 'body']))
      }
    })
    .filter(hit => hit.title || hit.url)
    .slice(0, limit)
}

function toolErrorText(part: ToolPart, result: Record<string, unknown>): string {
  const extractedError = extractToolErrorMessage(part.result)

  if (part.isError) {
    return extractedError || (typeof part.result === 'string' && part.result.trim()) || 'Tool returned an error.'
  }

  if (typeof result.error === 'string' && result.error.trim()) {
    return result.error.trim()
  }

  if (extractedError) {
    return extractedError
  }

  if (result.success === false || result.ok === false) {
    return firstStringField(result, ['message', 'reason', 'detail']) || 'Tool returned success=false.'
  }

  if (typeof result.status === 'string' && /\b(error|failed|failure)\b/i.test(result.status)) {
    return firstStringField(result, ['message', 'reason', 'detail']) || `Tool returned status "${result.status}".`
  }

  // A non-zero exit code alone is a weak failure signal: grep returns 1 on
  // no-match, diff returns 1 on differences, piped commands surface the last
  // stage's code, etc. — all routinely produce useful output and aren't
  // failures. Only treat it as an error when the command produced no real
  // output to show; otherwise render the output normally (not red).
  const exit = numberValue(result.exit_code)

  if (exit !== null && exit !== 0) {
    const hasOutput = Boolean(firstStringField(result, ['output', 'stdout', 'stderr'])?.trim())

    return hasOutput ? '' : `Command failed with exit code ${exit}.`
  }

  return ''
}

function toolStatus(part: ToolPart, resultRecord: Record<string, unknown>): ToolStatus {
  if (part.result === undefined) {
    return 'running'
  }

  return toolErrorText(part, resultRecord) ? 'error' : 'success'
}

function durationLabel(resultRecord: Record<string, unknown>): string | undefined {
  const seconds = numberValue(resultRecord.duration_s)

  if (seconds === null || seconds < 0) {
    return undefined
  }

  return formatDurationSeconds(seconds)
}

function toolPreviewTarget(toolName: string, args: Record<string, unknown>, result: Record<string, unknown>): string {
  const direct =
    firstStringField(result, ['preview', 'url', 'target']) ||
    firstStringField(args, ['preview', 'url', 'target', 'path', 'file', 'filepath']) ||
    firstStringField(result, ['path', 'file', 'filepath'])

  if (direct && (looksLikeUrl(direct) || looksLikePath(direct))) {
    return direct
  }

  if (toolName === 'browser_navigate' || toolName === 'web_extract' || toolName === 'web_search') {
    const explicit = firstStringField(args, ['url', 'search_term', 'query']) || firstStringField(result, ['url'])

    return looksLikeUrl(explicit) ? explicit : findFirstUrl(args, result)
  }

  if (isFileEditTool(toolName)) {
    return htmlPathFromInlineDiff(firstStringField(result, ['inline_diff', 'diff']))
  }

  return ''
}

function toolImageUrl(args: Record<string, unknown>, result: Record<string, unknown>): string {
  const candidate =
    firstStringField(result, ['image_url', 'url', 'path', 'image_path']) ||
    firstStringField(args, ['image_url', 'url', 'path'])

  if (!candidate) {
    return ''
  }

  // Only inline-render images the renderer can actually fetch: data URLs or
  // remote http(s). A bare filesystem path (e.g. vision_analyze's input image)
  // resolves against the dev-server origin and 404s — fall back to the tool's
  // codicon instead of a broken <img>.
  const isDataImage = candidate.toLowerCase().startsWith('data:image/')
  const isRemoteImage = /^https?:\/\//i.test(candidate) && /\.(png|jpe?g|gif|webp|bmp|svg)(\?|#|$)/i.test(candidate)

  return isDataImage || isRemoteImage ? candidate : ''
}

function stripAnsi(value: string): string {
  return value.replace(new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, 'g'), '')
}

export function stripInlineDiffChrome(value: string): string {
  return value
    ? stripAnsi(value)
        .replace(/^\s*┊\s*review diff\s*\n/i, '')
        .trim()
    : ''
}

function htmlPathFromInlineDiff(value: string): string {
  const cleaned = stripInlineDiffChrome(value)

  for (const match of cleaned.matchAll(/(?:^|\s)(?:[ab]\/)?([^\s]+\.html?)(?=\s|$)/gi)) {
    const candidate = match[1]?.trim()

    if (candidate) {
      return candidate
    }
  }

  return ''
}

function stripDividerLines(value: string): string {
  return value
    .split('\n')
    .filter(line => !/^[-=]{3,}\s*$/.test(line.trim()))
    .join('\n')
    .trim()
}

export function inlineDiffFromResult(result: unknown): string {
  const record = parseMaybeObject(result)

  for (const key of ['inline_diff', 'diff']) {
    const value = record[key]

    if (typeof value === 'string' && value.trim()) {
      return stripInlineDiffChrome(value)
    }
  }

  return ''
}

// Falls back to a string only when there's something concrete to render —
// counts of opaque items/fields are noise, not signal.
function minimalValueSummary(value: unknown): string {
  if (value == null) {
    return ''
  }

  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  return ''
}

function fallbackDetailText(args: unknown, result: unknown): string {
  const argContext = contextValue(args)
  const resultContext = contextValue(result)

  if (resultContext && resultContext !== argContext) {
    return resultContext
  }

  if (argContext) {
    return argContext
  }

  if (result !== undefined) {
    return formatToolResultSummary(result) || minimalValueSummary(result)
  }

  return formatToolResultSummary(args) || minimalValueSummary(args)
}

function cronScalar(value: unknown): string {
  if (typeof value === 'string') {
    return value.trim()
  }

  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value)
  }

  return ''
}

function formatCronTime(iso: string): string {
  const ts = Date.parse(iso)

  if (Number.isNaN(ts)) {
    return iso
  }

  return new Date(ts).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function cronjobSubtitle(argsRecord: Record<string, unknown>, resultRecord: Record<string, unknown>): string {
  const jobs = Array.isArray(resultRecord.jobs) ? resultRecord.jobs : null

  if (jobs) {
    return jobs.length ? `${jobs.length} cron job${jobs.length === 1 ? '' : 's'}` : 'No cron jobs'
  }

  const message = firstStringField(resultRecord, ['message'])

  if (message) {
    return message
  }

  const action = firstStringField(argsRecord, ['action']) || 'manage'
  const name = firstStringField(resultRecord, ['name']) || firstStringField(argsRecord, ['name', 'job_id'])
  const label = capitalize(action)

  return name ? `${label} ${name}` : `Cron ${action}`
}

function cronjobDetail(argsRecord: Record<string, unknown>, resultRecord: Record<string, unknown>): string {
  const jobs = Array.isArray(resultRecord.jobs) ? resultRecord.jobs : null

  if (jobs) {
    if (!jobs.length) {
      return 'No cron jobs scheduled'
    }

    return jobs
      .slice(0, 20)
      .map(job => {
        const row = isRecord(job) ? job : {}
        const name = firstStringField(row, ['name', 'id']) || 'job'
        const sched = firstStringField(row, ['schedule_display', 'schedule'])

        return sched ? `- ${name} · ${sched}` : `- ${name}`
      })
      .join('\n')
  }

  const nextRun = cronScalar(resultRecord.next_run_at)

  const rows: [string, string][] = [
    ['Schedule', cronScalar(resultRecord.schedule)],
    ['Repeat', cronScalar(resultRecord.repeat)],
    ['Delivery', cronScalar(resultRecord.deliver)],
    ['Next run', nextRun ? formatCronTime(nextRun) : '']
  ]

  const lines = rows.filter(([, value]) => value).map(([key, value]) => `${key}: ${value}`)

  return lines.length ? lines.join('\n') : fallbackDetailText(argsRecord, resultRecord)
}

function toolSubtitle(
  part: ToolPart,
  argsRecord: Record<string, unknown>,
  resultRecord: Record<string, unknown>
): string {
  const toolName = part.toolName

  if (toolName === 'browser_navigate') {
    const url =
      firstStringField(argsRecord, ['url', 'target']) ||
      firstStringField(resultRecord, ['url']) ||
      findFirstUrl(argsRecord, resultRecord)

    return url ? hostnameOf(url) : 'Navigated in browser'
  }

  if (toolName === 'browser_snapshot') {
    const snapshot = firstStringField(resultRecord, ['snapshot'])

    return snapshot ? summarizeBrowserSnapshot(snapshot) : 'Captured a browser accessibility snapshot'
  }

  if (toolName === 'browser_click') {
    const clicked = firstStringField(resultRecord, ['clicked']) || firstStringField(argsRecord, ['ref', 'target'])

    if (!clicked) {
      return 'Clicked on page'
    }

    return clicked.startsWith('@') ? `Clicked page element (internal ref ${clicked})` : `Clicked ${clicked}`
  }

  if (toolName === 'browser_fill' || toolName === 'browser_type') {
    const field = firstStringField(argsRecord, ['label', 'field', 'ref', 'target'])
    const value = firstStringField(argsRecord, ['value', 'text'])

    return (
      [field && `Field: ${field}`, value && `Value: ${compactPreview(value, 42)}`].filter(Boolean).join(' · ') ||
      'Filled page input'
    )
  }

  if (toolName === 'web_search') {
    const query = firstStringField(argsRecord, ['search_term', 'query']) || contextValue(argsRecord)

    return query ? `Query: ${query}` : 'Queried web sources'
  }

  if (toolName === 'terminal' || toolName === 'execute_code') {
    const output = firstStringField(resultRecord, ['output', 'stdout', 'stderr'])

    const lines = Array.isArray(resultRecord.lines)
      ? resultRecord.lines.filter((line): line is string => typeof line === 'string').join('\n')
      : ''

    const previewSource = (output || lines).trim()

    if (previewSource) {
      const firstMeaningfulLine = previewSource
        .split('\n')
        .map(line => line.trim())
        .find(line => line.length > 0)

      if (firstMeaningfulLine) {
        return compactPreview(firstMeaningfulLine, 160)
      }
    }

    const command = firstStringField(argsRecord, ['context', 'preview', 'command', 'code']) || contextValue(argsRecord)

    return command ? '' : 'Executed command'
  }

  if (toolName === 'read_file' || isFileEditTool(toolName)) {
    const isEdit = isFileEditTool(toolName)

    const path = isEdit
      ? fileEditPath(argsRecord, resultRecord)
      : firstStringField(argsRecord, ['path', 'file', 'filepath'])

    if (path) {
      return path
    }

    if (!isEdit) {
      return fallbackDetailText(argsRecord, resultRecord)
    }

    return inlineDiffFromResult(resultRecord) ? 'Changed file' : ''
  }

  if (toolName === 'web_extract') {
    const url =
      firstStringField(argsRecord, ['url']) ||
      firstStringField(resultRecord, ['url']) ||
      findFirstUrl(argsRecord, resultRecord)

    return url ? hostnameOf(url) : 'Fetched webpage'
  }

  if (toolName === 'cronjob') {
    return cronjobSubtitle(argsRecord, resultRecord)
  }

  // 裁定 36.3: the preference proposal reads as a card you can repeat back
  // ("待你确认 · 确认 3" + the first two questions), not a nameless tool row.
  if (isCheckinTool(toolName)) {
    return checkinSubtitleLine(resultRecord)
  }

  if (isSecretaryDispatchTool(toolName)) {
    const rawIntent = secretaryField(argsRecord, resultRecord, ['intent'])

    // 裁定 27: mutate_agenda 副标题 = 标题 + 起止（或「未写结束」）；
    // 裁定 33.5: cancel_matching 副标题另走 bulk 视图。
    if (rawIntent === 'mutate_agenda') {
      const action = firstStringField(argsRecord, ['action']) || firstStringField(resultRecord, ['action'])
      if (action === 'cancel_matching') {
        return cancelMatchingSubtitleLine(resultRecord)
      }
      return mutateSubtitleLine(resultRecord, argsRecord)
    }

    // 裁定 28.4: query_agenda 念回前几条；decide_pending 的标题已带目标，
    // 失败时 candidates 走可展开体（见 toolDetailText）。
    if (rawIntent === 'query_agenda') {
      return querySubtitleLine(resultRecord, argsRecord)
    }

    if (rawIntent === 'decide_pending') {
      return ''
    }

    // 裁定 30.1: project_status 念回项目 + 节奏，不伪装成派工。
    if (rawIntent === 'project_status') {
      return projectSubtitleLine(resultRecord)
    }

    // 裁定 32.5: plan_day 同样是办妥。
    if (rawIntent === 'plan_day') {
      return planDaySubtitleLine(resultRecord)
    }

    const dead = secretaryDeadDoor(resultRecord)
    const target = secretaryTarget(argsRecord, resultRecord)
    const intent = secretaryIntentLabel(secretaryField(argsRecord, resultRecord, ['intent']))
    const rawRoute = secretaryField(argsRecord, resultRecord, ['route'])
    const modelHint = secretaryField(argsRecord, resultRecord, ['model'])
    const inferredRoute =
      rawRoute ||
      (modelHint.toLowerCase().includes('workbuddy')
        ? 'workbuddy'
        : modelHint.toLowerCase().includes('fallback')
          ? 'fallback'
          : '')
    const route = secretaryRouteLabel(inferredRoute)
    const briefing = firstStringField(resultRecord, ['briefing'])
    const events = secretaryAgendaEvents(resultRecord)
    const eventHint =
      part.result !== undefined && intent.includes('refresh')
        ? events.length
          ? `${events.length} event${events.length === 1 ? '' : 's'}`
          : 'no events'
        : ''
    const preview = briefing
      ? compactPreview(briefing, 120)
      : eventHint || firstStringField(resultRecord, ['summary', 'message', 'detail'])

    if (dead) {
      return [target, intent, compactPreview(dead, 120)].filter(Boolean).join(' · ')
    }

    return [target, intent, route, preview].filter(Boolean).join(' · ')
  }

  return (
    compactPreview(formatToolResultSummary(part.result), 120) ||
    compactPreview(resultRecord, 120) ||
    compactPreview(argsRecord, 120) ||
    fallbackDetailText(argsRecord, resultRecord)
  )
}

function toolDetailLabel(toolName: string): string {
  if (toolName === 'web_search') {
    return 'Details'
  }

  if (toolName === 'browser_snapshot') {
    return 'Snapshot summary'
  }

  return ''
}

function toolDetailText(
  part: ToolPart,
  argsRecord: Record<string, unknown>,
  resultRecord: Record<string, unknown>
): string {
  // 裁定 28.4: `decide_pending` 命中多条时不猜——候选列进可展开体给用户挑。
  if (isSecretaryDispatchTool(part.toolName)) {
    const candidates = secretaryCandidateLines(resultRecord)

    if (candidates) {
      return candidates
    }
  }

  if (part.toolName === 'browser_snapshot') {
    const snapshot = firstStringField(resultRecord, ['snapshot'])

    return snapshot ? summarizeBrowserSnapshot(snapshot) : fallbackDetailText(argsRecord, resultRecord)
  }

  if (part.toolName === 'terminal' || part.toolName === 'execute_code') {
    // Streams are split out into ToolView.stdout / ToolView.stderr by
    // buildToolView so the renderer can label them separately. The merged
    // fallback here is only used when the backend doesn't expose either
    // stream individually.
    const output = firstStringField(resultRecord, ['output', 'stdout', 'stderr'])

    const lines = Array.isArray(resultRecord.lines)
      ? resultRecord.lines.filter((line): line is string => typeof line === 'string').join('\n')
      : ''

    if (output || lines) {
      return [output, lines].filter(Boolean).join('\n')
    }
  }

  if (part.toolName === 'web_extract') {
    const direct = firstStringField(resultRecord, ['content', 'text', 'markdown', 'body', 'summary', 'message'])

    if (direct) {
      return direct.replace(/\s*in\s+\d+(?:\.\d+)?s\s*$/i, '').trim()
    }

    const results = Array.isArray(resultRecord.results) ? resultRecord.results : []

    const aggregated = results
      .map(item => {
        const row = parseMaybeObject(item)

        return firstStringField(row, ['content', 'text', 'markdown', 'body'])
      })
      .filter(Boolean)
      .join('\n\n---\n\n')

    if (aggregated) {
      return aggregated
    }
  }

  if (part.toolName === 'read_file' && part.result !== undefined) {
    const content = firstStringField(resultRecord, ['content', 'text', 'data', 'body'])

    if (content) {
      return content
    }
  }

  if (isFileEditTool(part.toolName)) {
    if (inlineDiffFromResult(part.result)) {
      return ''
    }

    const summary = firstStringField(resultRecord, ['message', 'summary'])

    if (summary) {
      return summary
    }

    if (fileEditPath(argsRecord, resultRecord)) {
      return ''
    }

    return fallbackDetailText(argsRecord, resultRecord)
  }

  if (part.toolName === 'web_search') {
    const detail = fallbackDetailText(argsRecord, resultRecord)
    const seconds = numberValue(resultRecord.duration_s)
    const duration = seconds === null ? '' : formatDurationSeconds(seconds)

    if (!duration) {
      return detail
    }

    return detail
      .replace(/^\s*-\s*Duration\s+S\s*:\s*[-+]?[\d.]+(?:e[-+]?\d+)?\s*$/gim, `- Duration: ${duration}`)
      .replace(/\bDuration\s+S\s*:/gi, 'Duration:')
  }

  if (part.toolName === 'cronjob') {
    return cronjobDetail(argsRecord, resultRecord)
  }

  if (isSecretaryDispatchTool(part.toolName)) {
    const dead = secretaryDeadDoor(resultRecord)

    if (dead) {
      return dead
    }

    const briefing = firstStringField(resultRecord, ['briefing'])

    if (briefing) {
      return briefing
    }

    const events = secretaryAgendaEvents(resultRecord)

    if (events.length > 0) {
      return formatSecretaryEventLines(events)
    }

    const intent = secretaryField(argsRecord, resultRecord, ['intent'])

    if (part.result !== undefined && (intent === 'refresh_agenda' || !intent)) {
      return 'Agenda refreshed · no events in the today/tomorrow window.'
    }

    return firstStringField(resultRecord, ['summary', 'message', 'detail', 'text'])
  }

  return fallbackDetailText(argsRecord, resultRecord)
}

export function toolCopyPayload(part: ToolPart, view: ToolView): { label: string; text: string } {
  const copy = {
    command: translateNow('assistant.tool.copyCommand'),
    content: translateNow('assistant.tool.copyContent'),
    file: translateNow('assistant.tool.copyFile'),
    output: translateNow('assistant.tool.copyOutput'),
    path: translateNow('assistant.tool.copyPath'),
    query: translateNow('assistant.tool.copyQuery'),
    results: translateNow('assistant.tool.copyResults'),
    url: translateNow('assistant.tool.copyUrl'),
    generic: translateNow('common.copy')
  }

  const args = parseMaybeObject(part.args)
  const result = parseMaybeObject(part.result)
  const detail = view.detail.trim()
  const hasSubstantialOutput = detail.length > 16

  if (part.toolName === 'terminal' || part.toolName === 'execute_code') {
    if (hasSubstantialOutput) {
      return { label: copy.output, text: detail }
    }

    const command = firstStringField(args, ['command', 'code']) || contextValue(args)

    if (command) {
      return { label: copy.command, text: command }
    }
  }

  if (part.toolName === 'web_extract') {
    if (hasSubstantialOutput) {
      return { label: copy.content, text: detail }
    }

    const url = firstStringField(args, ['url', 'target']) || findFirstUrl(args, result)

    if (url) {
      return { label: copy.url, text: url }
    }
  }

  if (part.toolName === 'browser_navigate') {
    const url = firstStringField(args, ['url', 'target']) || findFirstUrl(args, result)

    if (url) {
      return { label: copy.url, text: url }
    }
  }

  if (part.toolName === 'web_search') {
    if (view.searchHits?.length) {
      const text = view.searchHits.map(hit => [hit.title, hit.url, hit.snippet].filter(Boolean).join('\n')).join('\n\n')

      return { label: copy.results, text }
    }

    const query = firstStringField(args, ['search_term', 'query']) || contextValue(args)

    if (query) {
      return { label: copy.query, text: query }
    }
  }

  if (part.toolName === 'read_file' && part.result !== undefined) {
    if (hasSubstantialOutput) {
      return { label: copy.file, text: detail }
    }

    const path = firstStringField(args, ['path', 'file', 'filepath'])

    if (path) {
      return { label: copy.path, text: path }
    }
  }

  if (isFileEditTool(part.toolName)) {
    if (view.inlineDiff.trim()) {
      return { label: copy.file, text: view.inlineDiff }
    }

    const path = fileEditPath(args, result)

    if (path) {
      return { label: copy.path, text: path }
    }
  }

  if (detail) {
    return { label: copy.output, text: detail }
  }

  return { label: copy.generic, text: view.title }
}

interface ToolTitleParts {
  action?: ToolTitleAction
  title: string
}

function titlePartsFromAction(title: string, action?: string): ToolTitleParts {
  if (!action) {
    return { title }
  }

  const actionStart = title.indexOf(action)

  if (actionStart < 0) {
    return { title }
  }

  return {
    action: {
      prefix: title.slice(0, actionStart),
      suffix: title.slice(actionStart + action.length),
      text: action
    },
    title
  }
}

function dynamicTitle(
  part: ToolPart,
  args: Record<string, unknown>,
  result: Record<string, unknown>,
  fallback: ToolTitleParts
): ToolTitleParts {
  const verb = (gerund: string, past: string) => (part.result === undefined ? gerund : past)

  const titledAction = (action: string, title: string): ToolTitleParts =>
    titlePartsFromAction(title, part.result === undefined ? action : undefined)

  // 裁定 36.3: 「别中午排会」走 plobi_checkin_respond —— 中栏是一张能念的
  // 提案卡（待你确认 · 确认 N），不是无名工具行。
  if (isCheckinTool(part.toolName)) {
    return checkinHeadline(result, part)
  }

  if (part.toolName === 'web_extract') {
    const url = findFirstUrl(args, result)
    const action = verb(translateNow('assistant.tool.actions.reading'), translateNow('assistant.tool.actions.read'))

    return url
      ? titledAction(action, translateNow('assistant.tool.titleTemplates.actionTarget', action, hostnameOf(url)))
      : fallback
  }

  if (part.toolName === 'browser_navigate') {
    const url = findFirstUrl(args, result)

    if (!url) {
      return fallback
    }

    const failed =
      part.isError || result.success === false || result.ok === false || Boolean(firstStringField(result, ['error']))

    if (failed) {
      const failAction = translateNow('assistant.tool.actions.failedToOpen')

      return titledAction(
        failAction,
        translateNow('assistant.tool.titleTemplates.actionTarget', failAction, hostnameOf(url))
      )
    }

    const action = verb(translateNow('assistant.tool.actions.opening'), translateNow('assistant.tool.actions.opened'))

    return titledAction(action, translateNow('assistant.tool.titleTemplates.actionTarget', action, hostnameOf(url)))
  }

  if (part.toolName === 'web_search') {
    const query = firstStringField(args, ['search_term', 'query']) || contextValue(args)

    const action = verb(
      translateNow('assistant.tool.actions.searching'),
      translateNow('assistant.tool.actions.searched')
    )

    return query
      ? titledAction(
          action,
          translateNow('assistant.tool.titleTemplates.actionQuoted', action, compactPreview(query, 48))
        )
      : fallback
  }

  if (part.toolName === 'read_file') {
    const target = readFileDisplayTarget(args, result)
    const action = verb(translateNow('assistant.tool.actions.reading'), translateNow('assistant.tool.actions.read'))

    return target
      ? titledAction(action, translateNow('assistant.tool.titleTemplates.actionTarget', action, target))
      : fallback
  }

  if (part.toolName === 'terminal' || part.toolName === 'execute_code') {
    const command =
      firstStringField(args, ['context', 'preview']) ||
      firstStringField(args, ['command', 'code']) ||
      contextValue(args)

    if (command) {
      const action =
        part.toolName === 'execute_code'
          ? verb(translateNow('assistant.tool.actions.runningCode'), translateNow('assistant.tool.actions.ranCode'))
          : verb(translateNow('assistant.tool.actions.running'), translateNow('assistant.tool.actions.ran'))

      return titledAction(
        action,
        translateNow(
          'assistant.tool.titleTemplates.actionCommand',
          action,
          compactPreview(summarizeShellCommand(command), 160)
        )
      )
    }
  }

  if (isFileEditTool(part.toolName)) {
    const path = fileEditPath(args, result)

    if (path) {
      return { title: fileEditBasename(path) }
    }
  }

  if (isSecretaryDispatchTool(part.toolName)) {
    const intent = firstStringField(args, ['intent']) || firstStringField(result, ['intent'])

    // 裁定 27: mutate_agenda 是办妥的事，不是派工——标题必须是
    // 已记下 / 已改 / 已取消，禁止 "Asking/Asked 日程秘书"。
    if (intent === 'mutate_agenda') {
      return mutateHeadline(result, args, part)
    }

    // 裁定 28.4: 查日程 / 确认忽略同样是当场答、当场办，不是派工。
    if (intent === 'query_agenda') {
      return queryHeadline(result, args, part)
    }

    if (intent === 'decide_pending') {
      return decideHeadline(result, args, part)
    }

    // 裁定 30.1: project_status 同样是当场答。
    if (intent === 'project_status') {
      return projectHeadline(result, part)
    }

    // 裁定 32.5: plan_day 同样是当场答。
    if (intent === 'plan_day') {
      return planDayHeadline(result, args, part)
    }

    const target = secretaryTarget(args, result)
    const action = verb('Asking', 'Asked')

    return titledAction(action, `${action} ${target}`)
  }

  return fallback
}

export function buildToolView(part: ToolPart, inlineDiff: string): ToolView {
  const argsRecord = parseMaybeObject(part.args)
  const resultRecord = parseMaybeObject(part.result)
  const meta = toolMeta(part.toolName)
  const status = toolStatus(part, resultRecord)
  const error = toolErrorText(part, resultRecord)
  const baseTitle = part.result === undefined ? meta.pending : meta.done

  const titleParts = dynamicTitle(
    part,
    argsRecord,
    resultRecord,
    titlePartsFromAction(baseTitle, part.result === undefined ? meta.pendingAction : undefined)
  )

  const title = titleParts.title
  const titleEnriched = title !== baseTitle
  const baseSubtitle = error || toolSubtitle(part, argsRecord, resultRecord)

  const keepSubtitleWithTitle =
    part.toolName === 'terminal' ||
    part.toolName === 'execute_code' ||
    isSecretaryDispatchTool(part.toolName) ||
    // 裁定 36.3: the checkin proposal card's subtitle carries the questions —
    // it must survive the title-enrichment drop.
    isCheckinTool(part.toolName) ||
    (isFileEditTool(part.toolName) && Boolean(baseSubtitle.trim()))

  const subtitle = titleEnriched && !error && !keepSubtitleWithTitle ? '' : baseSubtitle
  const detailBody = stripDividerLines(toolDetailText(part, argsRecord, resultRecord))

  const detail = error
    ? [error, detailBody]
        .filter(Boolean)
        .filter((value, index, list) => list.findIndex(entry => entry.trim() === value.trim()) === index)
        .join('\n\n')
    : detailBody

  const searchHits =
    part.toolName === 'web_search' && status !== 'error' ? extractSearchResults(part.result) : undefined

  const resultCount = status === 'error' ? null : toolResultCount(part, argsRecord, resultRecord)

  // For shell/code tools we surface stdout and stderr as separate labeled
  // streams in the renderer. Many CLIs use stderr for informational
  // messages (npm progress, git hints), so we deliberately don't paint
  // stderr destructively even though it's tagged.
  const rendersAnsi = part.toolName === 'terminal' || part.toolName === 'execute_code'
  const stdout = rendersAnsi ? firstStringField(resultRecord, ['stdout']) : ''
  const stderrRaw = rendersAnsi ? firstStringField(resultRecord, ['stderr']) : ''
  // Only attach stderr when the backend actually returned it as its own
  // field — otherwise the merged `detail` already covers it and double-
  // rendering would duplicate output.
  const hasSplitStreams = rendersAnsi && (Boolean(stdout) || Boolean(stderrRaw))

  return {
    countLabel: resultCount ? formatCountLabel(resultCount) : undefined,
    detail,
    detailLabel: error ? 'Error details' : toolDetailLabel(part.toolName),
    durationLabel: durationLabel(resultRecord),
    icon: meta.icon,
    imageUrl: toolImageUrl(argsRecord, resultRecord),
    inlineDiff,
    previewTarget: toolPreviewTarget(part.toolName, argsRecord, resultRecord),
    rawArgs: prettyJson(part.args),
    rawResult: prettyJson(part.result),
    rendersAnsi: rendersAnsi || undefined,
    searchHits: searchHits?.length ? searchHits : undefined,
    stderr: hasSplitStreams ? stderrRaw || undefined : undefined,
    stdout: hasSplitStreams ? stdout || undefined : undefined,
    status,
    subtitle,
    title,
    titleAction: titleParts.action,
    tone: meta.tone
  }
}
