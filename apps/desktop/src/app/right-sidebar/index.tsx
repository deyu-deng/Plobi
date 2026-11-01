import { useStore } from '@nanostores/react'
import { type ComponentProps, useState } from 'react'

import { TreeSkeleton } from '@/components/chat/skeletons'
import { ErrorBoundary } from '@/components/error-boundary'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { FileTypeIcon } from '@/components/ui/file-type-icon'
import { useDelayedTrue } from '@/hooks/use-delayed-true'
import { useI18n } from '@/i18n'
import { invalidateDesktopWorkspaceIndex } from '@/lib/desktop-fs'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { cn } from '@/lib/utils'
import { $panesFlipped } from '@/store/layout'
import { notifyError } from '@/store/notifications'
import { setCurrentSessionPreviewTarget, type PreviewTarget } from '@/store/preview'
import { $currentCwd } from '@/store/session'

import { SidebarPanelLabel } from '../shell/sidebar-label'

import { FileEntryContextMenu } from './file-actions'
import { fileIconTint } from './files/file-tint'
import { ProjectTree } from './files/tree'
import { useProjectFileSearch } from './files/use-project-file-search'
import { useProjectTree } from './files/use-project-tree'
import { WorkspaceOpenWith } from './files/workspace-open'

interface RightSidebarPaneProps {
  cwd?: string
  embedded?: boolean
  onActivateFile: (path: string) => void
  onActivateFolder: (path: string) => void
  onOpenFile?: (target: PreviewTarget) => void
  onToggleWorkspaceSidebar?: () => void
}

export function RightSidebarPane({
  cwd,
  embedded = false,
  onActivateFile,
  onActivateFolder,
  onOpenFile,
  onToggleWorkspaceSidebar
}: RightSidebarPaneProps) {
  const { t } = useI18n()
  const r = t.rightSidebar
  const panesFlipped = useStore($panesFlipped)
  const storeCwd = useStore($currentCwd).trim()
  // Explicit cwd wins: the workbench binds the tree to the current Agent, never
  // to the global remembered workspace.
  const currentCwd = (cwd ?? storeCwd).trim()

  // The file tree is simply "browse the session's working directory". If the
  // session has a cwd — a repo, a sibling worktree, or any folder — show it. A
  // bare/detached chat (resolveNewSessionCwd → '') has none, so it shows the
  // empty hint instead of whatever dir Plobi happens to run from.
  const hasWorkspace = Boolean(currentCwd)

  const {
    collapseAll,
    collapseNonce,
    data,
    effectiveCwd,
    loadChildren,
    openState,
    refreshRoot,
    rootError,
    rootLoading,
    setNodeOpen
  } = useProjectTree(hasWorkspace ? currentCwd : '')

  const cwdName =
    effectiveCwd
      .split(/[\\/]+/)
      .filter(Boolean)
      .pop() ?? effectiveCwd

  const canCollapse = Object.values(openState).some(Boolean)

  const previewFile = async (path: string) => {
    try {
      const preview = await normalizeOrLocalPreviewTarget(path, effectiveCwd || undefined)

      if (!preview) {
        throw new Error(r.couldNotPreview(path))
      }

      if (onOpenFile) {
        onOpenFile(preview)

        return
      }

      setCurrentSessionPreviewTarget(preview, 'file-browser', path)
    } catch (error) {
      notifyError(error, r.previewUnavailable)
    }
  }

  return (
    <aside
      aria-label={r.aria}
      className={cn(
        'before:pointer-events-none relative flex h-full w-full min-w-0 flex-col overflow-hidden text-(--ui-text-tertiary)',
        embedded
          ? 'bg-transparent'
          : cn(
              'border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) pt-(--titlebar-height)',
              panesFlipped
                ? 'border-r shadow-[inset_-0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
                : 'border-l shadow-[inset_0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
            )
      )}
    >
      <FilesystemTab
        canCollapse={canCollapse}
        collapseNonce={collapseNonce}
        cwd={effectiveCwd}
        cwdName={cwdName}
        data={data}
        error={rootError}
        hasWorkspace={hasWorkspace}
        loading={rootLoading}
        onActivateFile={onActivateFile}
        onActivateFolder={onActivateFolder}
        onCollapseAll={collapseAll}
        onLoadChildren={loadChildren}
        onNodeOpenChange={setNodeOpen}
        onPreviewFile={previewFile}
        onRefresh={() => void refreshRoot()}
        onToggleWorkspaceSidebar={onToggleWorkspaceSidebar}
        openState={openState}
      />
    </aside>
  )
}

interface FilesystemTabProps extends FileTreeBodyProps {
  canCollapse: boolean
  cwdName: string
  hasWorkspace: boolean
  onCollapseAll: () => void
  onRefresh: () => void
  onToggleWorkspaceSidebar?: () => void
}

// Sidebar palette + hover-reveal: header actions stay reachable while moving
// from the project label to the action buttons.
const HEADER_ACTION_CLASS =
  'text-sidebar-foreground/70 hover:bg-sidebar-accent! hover:text-sidebar-accent-foreground! focus-visible:ring-sidebar-ring'

const HEADER_ACTION_LABEL_REVEAL = `${HEADER_ACTION_CLASS} pointer-events-none opacity-0 transition-opacity focus-visible:pointer-events-auto focus-visible:opacity-100 group-focus-within/project-header:pointer-events-auto group-focus-within/project-header:opacity-100 group-hover/project-header:pointer-events-auto group-hover/project-header:opacity-100`

function FilesystemTab({
  canCollapse,
  collapseNonce,
  cwd,
  cwdName,
  data,
  error,
  hasWorkspace,
  loading,
  onActivateFile,
  onActivateFolder,
  onCollapseAll,
  onLoadChildren,
  onNodeOpenChange,
  onPreviewFile,
  onRefresh,
  onToggleWorkspaceSidebar,
  openState
}: FilesystemTabProps) {
  const { t } = useI18n()
  const r = t.rightSidebar
  const [query, setQuery] = useState('')
  const [searchRefresh, setSearchRefresh] = useState(0)
  const search = useProjectFileSearch(cwd, query, searchRefresh)
  const filtering = query.trim().length > 0

  // No working directory (a bare/detached chat) → no tree, just a terse hint.
  // Switching workspace is a project/worktree action, never a raw folder picker.
  if (!hasWorkspace) {
    return <PaneEmptyState label={r.noProjectOpen} />
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <RightSidebarSectionHeader>
        {onToggleWorkspaceSidebar ? (
          <Button
            aria-label={r.hideWorkspaceSidebar}
            className={HEADER_ACTION_CLASS}
            onClick={onToggleWorkspaceSidebar}
            size="icon-xs"
            title={r.hideWorkspaceSidebar}
            variant="ghost"
          >
            <Codicon name="layout-sidebar-right" size="0.8125rem" />
          </Button>
        ) : null}
        <div className="flex min-w-0 flex-1">
          <SidebarPanelLabel>{cwdName}</SidebarPanelLabel>
        </div>
        <WorkspaceOpenWith cwd={cwd} />
        <Button
          aria-label={r.refreshTree}
          className={HEADER_ACTION_LABEL_REVEAL}
          disabled={loading}
          onClick={() => {
            void invalidateDesktopWorkspaceIndex(cwd)
            setSearchRefresh(value => value + 1)
            onRefresh()
          }}
          size="icon-xs"
          title={r.refreshTree}
          variant="ghost"
        >
          <Codicon name="refresh" size="0.8125rem" spinning={loading} />
        </Button>
        <Button
          aria-label={r.collapseAll}
          className={cn(HEADER_ACTION_CLASS, !canCollapse && 'pointer-events-none opacity-0')}
          disabled={!canCollapse}
          onClick={onCollapseAll}
          size="icon-xs"
          title={r.collapseAll}
          variant="ghost"
        >
          <Codicon name="collapse-all" size="0.8125rem" />
        </Button>
      </RightSidebarSectionHeader>
      <div className="flex h-(--card-header-height) shrink-0 items-center px-1.5">
        <div className="relative w-full">
          <Codicon
            className="pointer-events-none absolute top-1/2 left-2 -translate-y-1/2 text-(--ui-text-tertiary)"
            name="search"
            size="0.8125rem"
          />
          <input
            aria-label={r.filterFiles}
            className="h-7 w-full appearance-none rounded-md border border-transparent bg-(--ui-bg-quaternary) pr-7 pl-7 text-xs text-foreground outline-none transition-colors placeholder:text-(--ui-text-tertiary) focus:border-(--ui-stroke-secondary) [&::-webkit-search-cancel-button]:hidden"
            onChange={event => setQuery(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Escape') {
                setQuery('')
              }
            }}
            placeholder={r.filterPlaceholder}
            type="search"
            value={query}
          />
          {search.loading ? (
            <Codicon
              className="absolute top-1/2 right-2 -translate-y-1/2 text-(--ui-text-tertiary)"
              name="loading"
              size="0.75rem"
              spinning
            />
          ) : query ? (
            <button
              aria-label={r.clearFilter}
              className="absolute top-1/2 right-1 grid size-5 -translate-y-1/2 place-items-center rounded-sm text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-row-hover-background) hover:text-foreground"
              onClick={() => setQuery('')}
              title={r.clearFilter}
              type="button"
            >
              <Codicon name="close" size="0.75rem" />
            </button>
          ) : null}
        </div>
      </div>
      {filtering ? (
        <FileSearchResults
          cwd={cwd}
          indexedFiles={search.indexedFiles}
          loading={search.loading}
          onPreviewFile={onPreviewFile}
          results={search.results}
          scannedDirs={search.scannedDirs}
          truncated={search.truncated}
        />
      ) : (
        <FileTreeBody
          collapseNonce={collapseNonce}
          cwd={cwd}
          data={data}
          error={error}
          loading={loading}
          onActivateFile={onActivateFile}
          onActivateFolder={onActivateFolder}
          onLoadChildren={onLoadChildren}
          onNodeOpenChange={onNodeOpenChange}
          onPreviewFile={onPreviewFile}
          onRetry={onRefresh}
          openState={openState}
        />
      )}
    </div>
  )
}

interface FileSearchResultsProps {
  cwd: string
  indexedFiles?: number
  loading: boolean
  onPreviewFile?: (path: string) => void
  results: ReturnType<typeof useProjectFileSearch>['results']
  scannedDirs: number
  truncated: boolean
}

const SEARCH_SKELETON_WIDTHS = ['w-28', 'w-20', 'w-36', 'w-24', 'w-32', 'w-16']

function FileSearchResults({
  cwd,
  indexedFiles,
  loading,
  onPreviewFile,
  results,
  scannedDirs,
  truncated
}: FileSearchResultsProps) {
  const { t } = useI18n()
  const r = t.rightSidebar

  if (loading && results.length === 0) {
    return (
      <div aria-busy="true" aria-label={r.searching} className="min-h-0 flex-1 px-1.5 py-1" role="status">
        {SEARCH_SKELETON_WIDTHS.map(width => (
          <div className="flex h-7 items-center gap-2 px-1" key={width}>
            <span className="size-3.5 shrink-0 rounded-sm bg-(--ui-bg-quaternary)" />
            <span className={cn('h-3 rounded-sm bg-(--ui-bg-quaternary)', width)} />
          </div>
        ))}
      </div>
    )
  }

  if (results.length === 0) {
    return <EmptyState body={r.searchEmptyBody} icon="search" title={r.searchEmpty} />
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-1" data-testid="file-search-results">
      {results.map(item => (
        <FileEntryContextMenu
          isDirectory={false}
          key={item.path}
          name={item.name}
          path={item.path}
          relativeTo={cwd}
        >
          <button
            className="row-hover flex h-7 w-full items-center gap-1.5 rounded-md px-2 text-left text-xs text-(--ui-text-secondary) hover:text-foreground"
            onClick={() => onPreviewFile?.(item.path)}
            title={item.path}
            type="button"
          >
            <FileTypeIcon className={fileIconTint(item.path)} path={item.path} size="0.875rem" />
            <span className="min-w-0 shrink truncate font-medium">{item.name}</span>
            {item.relativeDir ? (
              <span className="min-w-0 flex-1 truncate text-[0.66rem] text-(--ui-text-tertiary)">
                {item.relativeDir}
              </span>
            ) : null}
          </button>
        </FileEntryContextMenu>
      ))}
      {truncated ? (
        <p className="px-2 pt-1 pb-2 text-[0.66rem] leading-relaxed text-(--ui-text-tertiary)">
          {indexedFiles !== undefined ? r.searchIndexLimited(indexedFiles) : r.searchLimited(scannedDirs)}
        </p>
      ) : null}
    </div>
  )
}

export function RightSidebarSectionHeader({ children, className, ...props }: ComponentProps<'div'>) {
  return (
    <div className={cn('group/project-header flex h-7 shrink-0 items-center px-2.5', className)} {...props}>
      {children}
    </div>
  )
}

interface FileTreeBodyProps {
  collapseNonce: number
  cwd: string
  data: ReturnType<typeof useProjectTree>['data']
  error: string | null
  loading: boolean
  onActivateFile: (path: string) => void
  onActivateFolder: (path: string) => void
  onLoadChildren: (id: string) => void | Promise<void>
  onNodeOpenChange: (id: string, open: boolean) => void
  onPreviewFile?: (path: string) => void
  /** Force-reload the root. The hook also auto-retries while errored, so this
   *  is the impatient-user path. */
  onRetry?: () => void
  openState: ReturnType<typeof useProjectTree>['openState']
}

function FileTreeBody({
  collapseNonce,
  cwd,
  data,
  error,
  loading,
  onActivateFile,
  onActivateFolder,
  onLoadChildren,
  onNodeOpenChange,
  onPreviewFile,
  onRetry,
  openState
}: FileTreeBodyProps) {
  const { t } = useI18n()
  const r = t.rightSidebar
  // Stay blank for a beat, then skeleton — so a fast project switch doesn't
  // flash a jarring loading state.
  const showSkeleton = useDelayedTrue(loading && data.length === 0)

  if (!cwd) {
    return <EmptyState body={r.noProjectBody} title={r.noProjectTitle} />
  }

  if (error) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-4 text-center">
        <EmptyState body={r.unreadableBody(error)} title={r.unreadableTitle} />
        {onRetry && (
          <button
            className="text-[0.68rem] font-medium text-muted-foreground transition hover:text-foreground"
            onClick={onRetry}
            type="button"
          >
            {r.tryAgain}
          </button>
        )}
      </div>
    )
  }

  if (loading && data.length === 0) {
    return showSkeleton ? <FileTreeLoadingState /> : <div className="min-h-0 flex-1" />
  }

  if (data.length === 0) {
    return <EmptyState body={r.emptyBody} title={r.emptyTitle} />
  }

  return (
    <ErrorBoundary
      fallback={({ reset }) => (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-4 text-center">
          <EmptyState body={r.treeErrorBody} title={r.treeErrorTitle} />
          <button
            className="text-[0.68rem] font-medium text-muted-foreground transition hover:text-foreground"
            onClick={reset}
            type="button"
          >
            {r.tryAgain}
          </button>
        </div>
      )}
      key={cwd}
      label="file-tree"
    >
      <ProjectTree
        collapseNonce={collapseNonce}
        cwd={cwd}
        data={data}
        onActivateFile={onActivateFile}
        onActivateFolder={onActivateFolder}
        onLoadChildren={onLoadChildren}
        onNodeOpenChange={onNodeOpenChange}
        onPreviewFile={onPreviewFile}
        openState={openState}
      />
    </ErrorBoundary>
  )
}

function FileTreeLoadingState() {
  const { t } = useI18n()

  return (
    <div aria-label={t.rightSidebar.loadingTree} className="min-h-0 flex-1" role="status">
      <TreeSkeleton />
    </div>
  )
}

// Terse pane empty state ("No files" / "No diffs"): the panel label itself —
// same uppercase/tracking + dither dot — just muted instead of theme-primary,
// centered. Shared by the file tree and review panes so both read identically.
export function PaneEmptyState({ label }: { label: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-4">
      <SidebarPanelLabel className="pl-0 text-(--ui-text-quaternary)">{label}</SidebarPanelLabel>
    </div>
  )
}

// Richer empty/error state (title + body) for the file tree's read failures.
export function EmptyState({ body, icon, title }: { body: string; icon?: string; title?: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-1 px-4 text-center">
      {icon ? <Codicon className="mb-1 text-(--ui-text-quaternary)" name={icon} size="1.75rem" /> : null}
      {title && (
        <div className="text-[0.7rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground/75">{title}</div>
      )}
      <div className="text-[0.68rem] leading-relaxed text-muted-foreground/65">{body}</div>
    </div>
  )
}
