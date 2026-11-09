import { translateNow } from '@/i18n/runtime'

export interface CommandsCatalogSection {
  name: string
  pairs: [string, string][]
}

export interface CommandsCatalogLike {
  categories?: CommandsCatalogSection[]
  pairs?: [string, string][]
  skill_count?: number
  warning?: string
}

export interface DesktopSlashCompletion {
  display: string
  meta: string
  text: string
}

export interface DesktopThemeCommandOption {
  description: string
  label: string
  name: string
}

/**
 * Local client action a command resolves to. Each id maps to exactly one
 * handler in the dispatcher (`use-prompt-actions`), so adding a command never
 * means adding a branch to a switch ladder — you add a row here + a handler
 * keyed by the id.
 */
export type DesktopActionId =
  | 'branch'
  | 'browser'
  | 'handoff'
  | 'hatch'
  | 'help'
  | 'journey'
  | 'new'
  | 'pet'
  | 'profile'
  | 'skin'
  | 'title'
  | 'yolo'

/** A command fulfilled by opening a desktop overlay picker. */
export type DesktopPickerId = 'model' | 'session'

/** Why a known Plobi command has no desktop UI surface. */
export type DesktopUnavailableReason = 'advanced' | 'messaging' | 'settings' | 'terminal'

/**
 * How the desktop fulfils a command. This is the single discriminator the
 * dispatcher, popover, pills, and pickers all read — no parallel block-lists.
 *
 * - `action`     → handled by a local client handler (new chat, branch, …)
 * - `picker`     → opens an overlay (`/model`, `/resume`); a typed arg is
 *                  resolved by that picker instead of falling through
 * - `exec`       → runs on the backend via slash.exec / command.dispatch and
 *                  renders its text output inline
 * - `unavailable`→ a known command with genuinely no desktop UI (terminal-only,
 *                  messaging-only, …); shows a reason instead of executing
 */
export type DesktopCommandSurface =
  | { kind: 'action'; action: DesktopActionId }
  | { kind: 'picker'; picker: DesktopPickerId }
  | { kind: 'exec' }
  | { kind: 'unavailable'; reason: DesktopUnavailableReason }

export interface DesktopCommandSpec {
  /** Canonical command, leading slash included (e.g. `/resume`). */
  name: string
  /**
   * i18n path of the popover/help label; omitted for unavailable commands
   * (never surfaced). **The English text lives in the i18n catalog, not here**
   * — this table is read outside React too (the `/help` catalog filter), so a
   * literal here would put English on a Japanese or Traditional-Chinese screen.
   * See `desktop.slashCommands.*` in `src/i18n/`.
   */
  descriptionKey?: string
  aliases?: string[]
  surface: DesktopCommandSurface
  /**
   * Hide from the slash popover / completions while still letting it execute.
   * Used for picker commands reachable from chrome (the model picker lives on
   * the status bar), so the popover doesn't dead-end on inline completion.
   */
  hidden?: boolean
  /**
   * The command has an inline options "screen" (theme / personality / session /
   * platform / toolset list). Picking the bare command in the popover expands to
   * that argument step instead of committing — mirroring typing `/<cmd> ` by hand.
   */
  args?: boolean
}

const exec = (): DesktopCommandSurface => ({ kind: 'exec' })
const action = (id: DesktopActionId): DesktopCommandSurface => ({ kind: 'action', action: id })
const picker = (id: DesktopPickerId): DesktopCommandSurface => ({ kind: 'picker', picker: id })
const unavailable = (reason: DesktopUnavailableReason): DesktopCommandSurface => ({ kind: 'unavailable', reason })

/**
 * THE source of truth for desktop slash commands. Everything below — execution
 * gating, popover suggestions, catalog filtering, pill grouping, and the
 * dispatcher's behavior — derives from this one table.
 */
const DESKTOP_COMMAND_SPECS: readonly DesktopCommandSpec[] = [
  // Local client actions
  { name: '/new', descriptionKey: 'desktop.slashCommands.new', aliases: ['/reset'], surface: action('new') },
  {
    name: '/branch',
    descriptionKey: 'desktop.slashCommands.branch',
    aliases: ['/fork'],
    surface: action('branch')
  },
  { name: '/yolo', descriptionKey: 'desktop.slashCommands.yolo', surface: action('yolo') },
  {
    name: '/handoff',
    descriptionKey: 'desktop.slashCommands.handoff',
    surface: action('handoff'),
    args: true
  },
  { aliases: ['/profile'], name: '/agent', descriptionKey: 'desktop.slashCommands.agent', surface: action('profile') },
  { name: '/skin', descriptionKey: 'desktop.slashCommands.skin', surface: action('skin'), args: true },
  { name: '/title', descriptionKey: 'desktop.slashCommands.title', surface: action('title') },
  { name: '/help', descriptionKey: 'desktop.slashCommands.help', aliases: ['/commands'], surface: action('help') },
  {
    name: '/browser',
    descriptionKey: 'desktop.slashCommands.browser',
    surface: action('browser'),
    args: true
  },
  {
    name: '/journey',
    descriptionKey: 'desktop.slashCommands.journey',
    aliases: ['/learning', '/memory-graph'],
    surface: action('journey')
  },

  // Overlay pickers
  { name: '/model', descriptionKey: 'desktop.slashCommands.model', surface: picker('model'), hidden: true },
  {
    name: '/resume',
    descriptionKey: 'desktop.slashCommands.resume',
    aliases: ['/sessions', '/switch'],
    surface: picker('session'),
    args: true
  },

  // Backend-executed commands that render useful inline output
  {
    name: '/agents',
    descriptionKey: 'desktop.slashCommands.agents',
    aliases: ['/tasks'],
    surface: exec()
  },
  { name: '/background', descriptionKey: 'desktop.slashCommands.background', aliases: ['/bg', '/btw'], surface: exec() },
  { name: '/compress', descriptionKey: 'desktop.slashCommands.compress', surface: exec() },
  { name: '/debug', descriptionKey: 'desktop.slashCommands.debug', surface: exec() },
  { name: '/goal', descriptionKey: 'desktop.slashCommands.goal', surface: exec() },
  { name: '/personality', descriptionKey: 'desktop.slashCommands.personality', surface: exec(), args: true },
  {
    name: '/pet',
    descriptionKey: 'desktop.slashCommands.pet',
    surface: action('pet'),
    args: true
  },
  {
    name: '/hatch',
    descriptionKey: 'desktop.slashCommands.hatch',
    aliases: ['/generate-pet'],
    surface: action('hatch')
  },
  { name: '/queue', descriptionKey: 'desktop.slashCommands.queue', aliases: ['/q'], surface: exec() },
  { name: '/retry', descriptionKey: 'desktop.slashCommands.retry', surface: exec() },
  { name: '/rollback', descriptionKey: 'desktop.slashCommands.rollback', surface: exec() },
  { name: '/save', descriptionKey: 'desktop.slashCommands.save', surface: exec() },
  { name: '/status', descriptionKey: 'desktop.slashCommands.status', surface: exec() },
  { name: '/steer', descriptionKey: 'desktop.slashCommands.steer', surface: exec() },
  { name: '/stop', descriptionKey: 'desktop.slashCommands.stop', surface: exec() },
  { name: '/tools', descriptionKey: 'desktop.slashCommands.tools', surface: exec(), args: true },
  { name: '/undo', descriptionKey: 'desktop.slashCommands.undo', surface: exec() },
  { name: '/usage', descriptionKey: 'desktop.slashCommands.usage', surface: exec() },
  { name: '/version', descriptionKey: 'desktop.slashCommands.version', surface: exec() },

  // No desktop surface, but carry an alias (underscore spelling variants).
  { name: '/reload-mcp', aliases: ['/reload_mcp'], surface: unavailable('advanced') },
  { name: '/reload-skills', aliases: ['/reload_skills'], surface: unavailable('advanced') }
]

// Known commands with no desktop surface (and no alias) — a flat name list
// per reason beats 40 identical object literals.
const NO_DESKTOP_SURFACE: Record<DesktopUnavailableReason, readonly string[]> = {
  terminal: [
    '/busy',
    '/clear',
    '/compact',
    '/config',
    '/copy',
    '/cron',
    '/details',
    '/exit',
    '/footer',
    '/gateway',
    '/history',
    '/image',
    '/indicator',
    '/logs',
    '/mouse',
    '/paste',
    '/platforms',
    '/plugins',
    '/quit',
    '/redraw',
    '/reload',
    '/restart',
    '/sb',
    '/set-home',
    '/sethome',
    '/snap',
    '/snapshot',
    '/statusbar',
    '/toolsets',
    '/update',
    '/verbose'
  ],
  messaging: ['/approve', '/deny'],
  settings: ['/skills', '/pets'],
  advanced: ['/curator', '/fast', '/insights', '/kanban', '/reasoning', '/voice']
}

const ALL_SPECS: readonly DesktopCommandSpec[] = [
  ...DESKTOP_COMMAND_SPECS,
  ...(Object.entries(NO_DESKTOP_SURFACE) as [DesktopUnavailableReason, readonly string[]][]).flatMap(
    ([reason, names]) => names.map(name => ({ name, surface: unavailable(reason) }))
  )
]

const SPEC_BY_NAME = new Map<string, DesktopCommandSpec>(ALL_SPECS.map(spec => [spec.name, spec]))

const ALIAS_TO_CANONICAL = new Map<string, string>(
  ALL_SPECS.flatMap(spec => (spec.aliases ?? []).map(alias => [alias, spec.name] as const))
)

const UNAVAILABLE_MESSAGE: Record<DesktopUnavailableReason, (command: string) => string> = {
  advanced: command =>
    `${command} is not shown in the desktop slash palette. Use the relevant desktop control or terminal interface instead.`,
  messaging: command => `${command} is only used from messaging platforms.`,
  settings: command => `${command} is managed from the desktop sidebar.`,
  terminal: command => `${command} is only available in the terminal interface.`
}

const PICKER_UNAVAILABLE_MESSAGE: Record<DesktopPickerId, (command: string) => string> = {
  model: command => `${command} uses the desktop model picker instead of a slash command.`,
  session: command => `${command} uses the desktop session picker instead of a slash command.`
}

function normalizeCommand(command: string): string {
  const trimmed = command.trim()
  const base = (trimmed.startsWith('/') ? trimmed : `/${trimmed}`).split(/\s+/, 1)[0]?.toLowerCase() || ''

  return base
}

export function canonicalDesktopSlashCommand(command: string): string {
  const normalized = normalizeCommand(command)

  return ALIAS_TO_CANONICAL.get(normalized) || normalized
}

/** Resolve a command (or alias) to its desktop spec, or null for unknown/extension commands. */
export function resolveDesktopCommand(command: string): DesktopCommandSpec | null {
  return SPEC_BY_NAME.get(canonicalDesktopSlashCommand(command)) ?? null
}

function isKnownPlobiSlashCommand(command: string): boolean {
  const normalized = normalizeCommand(command)

  return SPEC_BY_NAME.has(normalized) || ALIAS_TO_CANONICAL.has(normalized)
}

/**
 * An "extension" command is anything the backend surfaces that is NOT one of
 * Plobi' built-in slash commands — i.e. skill commands (`/gif-search`,
 * `/codex`, …) and user-defined quick commands. These are user-activated, so
 * they appear in the desktop slash palette and execute when typed.
 */
export function isDesktopSlashExtensionCommand(command: string): boolean {
  const normalized = normalizeCommand(command)

  if (!normalized || normalized === '/') {
    return false
  }

  return !isKnownPlobiSlashCommand(normalized)
}

/** Gates execution: true unless the command is a known no-desktop-surface command. */
export function isDesktopSlashCommand(command: string): boolean {
  const spec = resolveDesktopCommand(command)

  if (spec) {
    return spec.surface.kind !== 'unavailable'
  }

  return isDesktopSlashExtensionCommand(command)
}

/** Gates discovery in the popover/completions. */
export function isDesktopSlashSuggestion(command: string): boolean {
  const normalized = normalizeCommand(command)

  // Aliases stay hidden so the popover isn't cluttered with duplicates.
  if (ALIAS_TO_CANONICAL.has(normalized)) {
    return false
  }

  const spec = SPEC_BY_NAME.get(normalized)

  if (spec) {
    return spec.surface.kind !== 'unavailable' && !spec.hidden
  }

  // Skill / quick commands the backend provides.
  return isDesktopSlashExtensionCommand(normalized)
}

/**
 * True for commands the desktop fulfils by opening an overlay picker
 * (`/model`, `/resume`/`/sessions`/`/switch`). Optionally pin to one picker.
 */
export function isPickerCommand(command: string, picker?: DesktopPickerId): boolean {
  const surface = resolveDesktopCommand(command)?.surface

  if (surface?.kind !== 'picker') {
    return false
  }

  return picker ? surface.picker === picker : true
}

/** Back-compat shim for the model picker check. */
export function isModelPickerCommand(command: string): boolean {
  return isPickerCommand(command, 'model')
}

export function desktopSlashUnavailableMessage(command: string): string | null {
  const canonical = canonicalDesktopSlashCommand(command)
  const surface = SPEC_BY_NAME.get(canonical)?.surface

  if (!surface) {
    return null
  }

  if (surface.kind === 'unavailable') {
    return UNAVAILABLE_MESSAGE[surface.reason](canonical)
  }

  if (surface.kind === 'picker') {
    return PICKER_UNAVAILABLE_MESSAGE[surface.picker](canonical)
  }

  return null
}

export function desktopSlashDescription(command: string, fallback = ''): string {
  const spec = SPEC_BY_NAME.get(canonicalDesktopSlashCommand(command))

  if (!spec?.descriptionKey) {
    return fallback
  }

  // Non-React module (the `/help` catalog filter runs outside the tree), so it
  // resolves against the runtime locale rather than a hook.
  const translated = translateNow(spec.descriptionKey)

  // `translateNow` returns the key itself when the path is missing — that
  // would put a dotted path on screen, so fall back instead.
  return translated === spec.descriptionKey ? fallback : translated
}

/**
 * True when picking the bare command should expand to its inline argument
 * options (theme / personality / session / platform / toolset) rather than
 * committing immediately. Lets the popover act as a two-step picker.
 */
export function desktopSlashCommandTakesArgs(command: string): boolean {
  return resolveDesktopCommand(command)?.args ?? false
}

export function desktopSkinSlashCompletions(
  themes: DesktopThemeCommandOption[],
  activeThemeName: string,
  argPrefix: string
): DesktopSlashCompletion[] {
  const prefix = argPrefix.trim().toLowerCase()

  const commands: DesktopSlashCompletion[] = [
    {
      text: '/skin list',
      display: '/skin list',
      meta: translateNow('desktop.slashCommands.skinList')
    },
    {
      text: '/skin next',
      display: '/skin next',
      meta: translateNow('desktop.slashCommands.skinNext')
    },
    ...themes.map(theme => ({
      text: `/skin ${theme.name}`,
      display: `/skin ${theme.name}`,
      meta: `${theme.label}${theme.name === activeThemeName ? ` (${translateNow('desktop.slashCommands.currentTheme')})` : ''} - ${theme.description}`
    }))
  ]

  if (!prefix) {
    return commands
  }

  return commands.filter(item => item.text.slice('/skin '.length).toLowerCase().startsWith(prefix))
}

export function filterDesktopCommandsCatalog(catalog: CommandsCatalogLike): CommandsCatalogLike {
  const categories = catalog.categories
    ?.map(section => ({
      ...section,
      pairs: section.pairs
        .filter(([command]) => isDesktopSlashSuggestion(command))
        .map(([command, description]) => [command, desktopSlashDescription(command, description)] as [string, string])
    }))
    .filter(section => section.pairs.length > 0)

  const pairs = catalog.pairs
    ?.filter(([command]) => isDesktopSlashSuggestion(command))
    .map(([command, description]) => [command, desktopSlashDescription(command, description)] as [string, string])

  // Recount skill commands from the filtered output so /help's footer reflects
  // what the user actually sees. Backend's skill_count includes commands the
  // desktop hides (terminal-only, picker-owned, advanced), producing a footer
  // like "60 skill commands available" while only ~29 appear in the list.
  const filteredCommands = new Set<string>()

  for (const section of categories ?? []) {
    for (const [command] of section.pairs) {
      filteredCommands.add(canonicalDesktopSlashCommand(command))
    }
  }

  for (const [command] of pairs ?? []) {
    filteredCommands.add(canonicalDesktopSlashCommand(command))
  }

  let skillCount = 0

  for (const command of filteredCommands) {
    if (isDesktopSlashExtensionCommand(command)) {
      skillCount += 1
    }
  }

  const hasSkillCount = catalog.skill_count !== undefined || skillCount > 0

  return {
    ...catalog,
    ...(categories ? { categories } : {}),
    ...(pairs ? { pairs } : {}),
    ...(hasSkillCount ? { skill_count: skillCount } : {})
  }
}
