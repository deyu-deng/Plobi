import { atom, computed } from 'nanostores'

import { readKey, writeKey } from '@/lib/storage'
import { $agentScope } from '@/store/agent-scope'
import { $currentCwd } from '@/store/session'

import { setTerminalTakeover } from '../store'

import { seedAgentTerminalCommand } from './agent-terminal-stream'

/** One in-app terminal tab. `id` is the renderer-side handle (distinct from the
 *  PTY session id the main process mints); each instance owns its own shell. */
export interface TerminalEntry {
  id: string
  /** Display label. `auto` adopts the resolved shell name until the user renames. */
  title: string
  auto: boolean
  /** Working directory, snapshotted once at creation. Terminals live outside
   *  session/project state — the only thing they inherit is this initial cwd
   *  (the project root if opened in one, else the backend's default). Switching
   *  Agents never moves or recreates a terminal; it only decides which tab strip
   *  shows it. */
  cwd: string
  /** Serialized xterm scrollback from the last session, replayed on relaunch so
   *  the tab reopens with its recent history (VS Code parity). Processes are NOT
   *  revived — a fresh shell starts beneath the restored buffer. Captured live
   *  for user tabs only; agent mirrors stay runtime-only. */
  reviveBuffer?: string
  /** `user` = interactive PTY shell. `agent` = read-only mirror of an agent
   *  background process (`terminal(background=true)`), keyed by `procId`. */
  kind: 'user' | 'agent'
  procId?: string
  /** Owning Agent scope (`'l1'` or an L2 roster id). Plobi has no "session"
   *  concept — one Agent is one mouth — so the tab strip is partitioned by
   *  Agent, not by conversation: every tab belongs to exactly one mouth, and
   *  switching Agents swaps the strip while all PTYs stay alive. */
  scope: string
}

interface PersistedTerminalEntry {
  auto: boolean
  cwd: string
  id: string
  reviveBuffer?: string
  scope: string
  title: string
}

interface PersistedTerminalState {
  /** Active tab per Agent scope. */
  activeByScope: Record<string, null | string>
  terminals: PersistedTerminalEntry[]
}

const TERMINALS_STORAGE_KEY = 'plobi.desktop.terminals.v1'

// Cap a single tab's replayed history so the persisted layout can't blow the
// localStorage quota. Roughly mirrors VS Code's persistentSessionScrollback
// default (100 lines) once the serialized escape codes are counted in.
const MAX_REVIVE_BUFFER_CHARS = 48_000

const L1_SCOPE = 'l1'

function sanitizePersistedTerminal(value: unknown): PersistedTerminalEntry | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const record = value as Record<string, unknown>
  const id = typeof record.id === 'string' ? record.id.trim() : ''
  const title = typeof record.title === 'string' ? record.title.trim() : ''
  const cwd = typeof record.cwd === 'string' ? record.cwd : ''
  const reviveBuffer = typeof record.reviveBuffer === 'string' ? record.reviveBuffer : undefined
  // Archives written before tabs were partitioned by Agent carry no scope —
  // they all belonged to the L1 window, so fold them there instead of dropping.
  const scope = typeof record.scope === 'string' && record.scope.trim() ? record.scope.trim() : L1_SCOPE

  if (!id) {
    return null
  }

  return {
    auto: typeof record.auto === 'boolean' ? record.auto : true,
    cwd,
    id,
    ...(reviveBuffer ? { reviveBuffer } : {}),
    scope,
    title: title || 'Terminal'
  }
}

function loadPersistedTerminals(): PersistedTerminalState {
  const fallback: PersistedTerminalState = { activeByScope: {}, terminals: [] }
  const raw = readKey(TERMINALS_STORAGE_KEY)

  if (!raw) {
    return fallback
  }

  try {
    const parsed = JSON.parse(raw) as unknown

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return fallback
    }

    const record = parsed as Record<string, unknown>

    const terminals = Array.isArray(record.terminals)
      ? record.terminals.map(sanitizePersistedTerminal).filter((term): term is PersistedTerminalEntry => Boolean(term))
      : []

    const ids = new Set(terminals.map(term => term.id))
    const activeByScope: Record<string, null | string> = {}

    if (record.activeByScope && typeof record.activeByScope === 'object' && !Array.isArray(record.activeByScope)) {
      for (const [scope, id] of Object.entries(record.activeByScope as Record<string, unknown>)) {
        if (typeof id === 'string' && ids.has(id)) {
          activeByScope[scope] = id
        }
      }
    }

    // Pre-10-31 archives carried one global `activeTerminalId`.
    const legacyActive = typeof record.activeTerminalId === 'string' ? record.activeTerminalId : null

    if (legacyActive && ids.has(legacyActive)) {
      activeByScope[L1_SCOPE] = activeByScope[L1_SCOPE] ?? legacyActive
    }

    for (const scope of new Set(terminals.map(term => term.scope))) {
      if (activeByScope[scope] == null) {
        activeByScope[scope] = terminals.find(term => term.scope === scope)?.id ?? null
      }
    }

    return { activeByScope, terminals }
  } catch {
    return fallback
  }
}

// Persist synchronously on every change (the app-wide convention — see panes.ts
// / layout.ts). Capturing history this way means a snapshot is already on disk
// well before the renderer tears down, so app quit needs no unload hook.
function persistTerminals(list: readonly TerminalEntry[], activeByScope: Record<string, null | string>) {
  const terminals = list
    .filter(term => term.kind === 'user')
    .map(term => ({
      auto: term.auto,
      cwd: term.cwd,
      id: term.id,
      ...(term.reviveBuffer ? { reviveBuffer: term.reviveBuffer } : {}),
      scope: term.scope,
      title: term.title
    }))

  if (!terminals.length) {
    writeKey(TERMINALS_STORAGE_KEY, null)

    return
  }

  const ids = new Set(terminals.map(term => term.id))
  const persistedActive: Record<string, null | string> = {}

  for (const [scope, id] of Object.entries(activeByScope)) {
    if (id && ids.has(id)) {
      persistedActive[scope] = id
    }
  }

  for (const scope of new Set(terminals.map(term => term.scope))) {
    persistedActive[scope] = persistedActive[scope] ?? terminals.find(term => term.scope === scope)?.id ?? null
  }

  writeKey(TERMINALS_STORAGE_KEY, JSON.stringify({ activeByScope: persistedActive, terminals }))
}

const restored = loadPersistedTerminals()

export const $terminals = atom<readonly TerminalEntry[]>(
  restored.terminals.map(term => ({ ...term, kind: 'user' as const }))
)

/** Active tab id, per Agent scope. Kept as a map so switching Agents never
 *  loses where you were in the one you left. */
export const $activeTerminalByScope = atom<Record<string, null | string>>(restored.activeByScope)

/** The active tab of the Agent currently on screen. */
export const $activeTerminalId = computed(
  [$activeTerminalByScope, $agentScope],
  (byScope, scope) => byScope[scope] ?? null
)

/** The tab strip of the Agent currently on screen. Every other Agent's tabs
 *  stay mounted and their shells stay alive — they're just not listed here. */
export const $scopeTerminals = computed([$terminals, $agentScope], (list, scope) =>
  list.filter(term => term.scope === scope)
)

$terminals.subscribe(list => persistTerminals(list, $activeTerminalByScope.get()))
$activeTerminalByScope.subscribe(active => persistTerminals($terminals.get(), active))

export const $activeTerminal = computed(
  [$terminals, $activeTerminalId],
  (list, id) => list.find(term => term.id === id) ?? null
)

const newId = () =>
  globalThis.crypto?.randomUUID?.() ?? `term-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`

function setActiveTerminalForScope(scope: string, id: null | string): void {
  const current = $activeTerminalByScope.get()

  if (current[scope] === id) {
    return
  }

  $activeTerminalByScope.set({ ...current, [scope]: id })
}

/** Append a fresh terminal to the current Agent's strip and focus it. Captures
 *  the current cwd once (its only tie to project state); pass an explicit cwd or
 *  scope to override. Returns the id. */
export function createTerminal(cwd: string = $currentCwd.get(), scope: string = $agentScope.get()): string {
  const id = newId()
  $terminals.set([...$terminals.get(), { id, title: 'Terminal', auto: true, cwd, kind: 'user', scope }])
  setActiveTerminalForScope(scope, id)

  return id
}

// Procs we've already surfaced a tab for — so closing an agent tab doesn't
// resurrect it on the next poll while the process is still running.
const surfacedProcs = new Set<string>()

const findByProc = (procId: string) => $terminals.get().find(term => term.procId === procId)

/** Auto-surface an agent background process as a read-only tab in `scope` —
 *  once. Returns the tab id, or null if it was already surfaced and the user
 *  has since closed it. */
export function ensureAgentTerminal(procId: string, title: string, scope: string = $agentScope.get()): string | null {
  const existing = findByProc(procId)

  if (existing) {
    return existing.id
  }

  if (surfacedProcs.has(procId)) {
    return null
  }

  surfacedProcs.add(procId)
  const id = newId()
  $terminals.set([
    ...$terminals.get(),
    { id, title: title || 'agent', auto: false, cwd: '', kind: 'agent', procId, scope }
  ])

  return id
}

/** Open + focus an agent process's tab (the status-stack link), recreating it if
 *  the user had closed it. Opens the pane. */
export function openAgentTerminal(procId: string, title: string, scope: string = $agentScope.get()): void {
  surfacedProcs.add(procId)
  seedAgentTerminalCommand(procId, title)
  let id = findByProc(procId)?.id

  if (!id) {
    id = newId()
    $terminals.set([
      ...$terminals.get(),
      { id, title: title || 'agent', auto: false, cwd: '', kind: 'agent', procId, scope }
    ])
  }

  setActiveTerminalForScope(scope, id)
  setTerminalTakeover(true)
}

/** Guarantee the current Agent has at least one tab when the pane opens.
 *  If a status-stack click already opened an agent tab, don't create a
 *  second, unrelated user shell just because the pane became visible. */
export function ensureTerminal(): void {
  const scope = $agentScope.get()

  if (!$terminals.get().some(term => term.scope === scope)) {
    createTerminal(undefined, scope)
  }
}

export function selectTerminal(id: string): void {
  const term = $terminals.get().find(entry => entry.id === id)

  if (term) {
    setActiveTerminalForScope(term.scope, id)
  }
}

/** Move the active tab of the current Agent by `direction` (+1 next / -1 prev),
 *  wrapping around inside that Agent's strip only. */
export function cycleTerminal(direction: 1 | -1): void {
  const list = $scopeTerminals.get()

  if (list.length < 2) {
    return
  }

  const activeId = $activeTerminalId.get()
  const current = Math.max(
    0,
    list.findIndex(term => term.id === activeId)
  )

  setActiveTerminalForScope($agentScope.get(), list[(current + direction + list.length) % list.length].id)
}

/** Drop a terminal. Focus slides to the neighbor that fills its slot **within
 *  the same Agent**; closing the last one closes the whole pane. */
export function closeTerminal(id: string): void {
  const list = $terminals.get()
  const index = list.findIndex(term => term.id === id)

  if (index < 0) {
    return
  }

  const closing = list[index]
  const next = list.filter(term => term.id !== id)
  $terminals.set(next)

  if ($activeTerminalByScope.get()[closing.scope] === id) {
    const before = list.slice(0, index).filter(term => term.scope === closing.scope)
    const after = list.slice(index + 1).filter(term => term.scope === closing.scope)
    const fallback = after[0] ?? before[before.length - 1]

    setActiveTerminalForScope(closing.scope, fallback?.id ?? null)
  }

  if (!next.some(term => term.scope === closing.scope)) {
    setTerminalTakeover(false)
  }
}

/** Close the read-only agent tab mirroring a background process. The agent
 *  drives this via the desktop-gated `close_terminal` tool → `terminal.close`.
 *  The process is NOT killed — only the view is dropped; `surfacedProcs` keeps
 *  it from auto-resurfacing, and the status-stack row can reopen it on demand.
 *  No-op when no such tab exists. */
export function closeAgentTerminalByProc(procId: string): boolean {
  const term = $terminals.get().find(t => t.kind === 'agent' && t.procId === procId)

  if (!term) {
    return false
  }

  closeTerminal(term.id)

  return true
}

export function closeActiveTerminal(): void {
  const id = $activeTerminalId.get()

  if (id) {
    closeTerminal(id)
  }
}

/** Close every tab of the current Agent. Other Agents' shells are untouched. */
export function closeAllTerminals(): void {
  const scope = $agentScope.get()
  const list = $terminals.get()
  const next = list.filter(term => term.scope !== scope)

  if (next.length === list.length) {
    return
  }

  $terminals.set(next)
  setActiveTerminalForScope(scope, null)
  setTerminalTakeover(false)
}

/** Keep only `id` among the current Agent's tabs; other Agents keep theirs. */
export function closeOtherTerminals(id: string): void {
  const keep = $terminals.get().find(term => term.id === id)

  if (!keep) {
    return
  }

  $terminals.set($terminals.get().filter(term => term.id === id || term.scope !== keep.scope))
  setActiveTerminalForScope(keep.scope, keep.id)
}

/** Record the latest serialized scrollback for a tab so it can be replayed on
 *  the next launch. Oversized buffers are tail-trimmed to stay under the storage
 *  budget; only user tabs ever carry one. */
export function updateTerminalReviveBuffer(id: string, reviveBuffer: string): void {
  const capped =
    reviveBuffer.length > MAX_REVIVE_BUFFER_CHARS ? reviveBuffer.slice(-MAX_REVIVE_BUFFER_CHARS) : reviveBuffer

  $terminals.set(
    $terminals.get().map(term => (term.id === id && term.kind === 'user' ? { ...term, reviveBuffer: capped } : term))
  )
}

export function renameTerminal(id: string, title: string): void {
  const trimmed = title.trim()

  $terminals.set(
    $terminals.get().map(term => (term.id === id ? { ...term, title: trimmed || term.title, auto: false } : term))
  )
}

/** A live terminal reports its resolved shell; adopt it as the label only while
 *  the user hasn't named the tab themselves. */
export function reportTerminalShell(id: string, shell: string): void {
  const name = shell.trim()

  if (!name) {
    return
  }

  $terminals.set($terminals.get().map(term => (term.id === id && term.auto ? { ...term, title: name } : term)))
}
