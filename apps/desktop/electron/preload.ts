import { contextBridge, ipcRenderer, webUtils } from 'electron'

contextBridge.exposeInMainWorld('plobiDesktop', {
  getConnection: profile => ipcRenderer.invoke('plobi:connection', profile),
  revalidateConnection: () => ipcRenderer.invoke('plobi:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('plobi:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('plobi:gateway:ws-url', profile),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('plobi:window:openSession', sessionId, opts),
  openNewSessionWindow: () => ipcRenderer.invoke('plobi:window:openNewSession'),
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('plobi:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('plobi:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('plobi:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('plobi:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('plobi:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('plobi:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('plobi:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('plobi:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('plobi:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('plobi:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('plobi:pet-overlay:control', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('plobi:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('plobi:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('plobi:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('plobi:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('plobi:connection-config:test', payload),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('plobi:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('plobi:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('plobi:connection-config:oauth-logout', remoteUrl),
  // Plobi Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('plobi:cloud:status'),
    login: () => ipcRenderer.invoke('plobi:cloud:login'),
    logout: () => ipcRenderer.invoke('plobi:cloud:logout'),
    discover: org => ipcRenderer.invoke('plobi:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('plobi:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    get: () => ipcRenderer.invoke('plobi:profile:get'),
    set: name => ipcRenderer.invoke('plobi:profile:set', name)
  },
  api: request => ipcRenderer.invoke('plobi:api', request),
  notify: payload => ipcRenderer.invoke('plobi:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('plobi:requestMicrophoneAccess'),
  readFileDataUrl: filePath => ipcRenderer.invoke('plobi:readFileDataUrl', filePath),
  readFileText: filePath => ipcRenderer.invoke('plobi:readFileText', filePath),
  selectPaths: options => ipcRenderer.invoke('plobi:selectPaths', options),
  writeClipboard: text => ipcRenderer.invoke('plobi:writeClipboard', text),
  saveImageFromUrl: url => ipcRenderer.invoke('plobi:saveImageFromUrl', url),
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('plobi:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('plobi:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('plobi:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('plobi:watchPreviewFile', url),
  stopPreviewFileWatch: id => ipcRenderer.invoke('plobi:stopPreviewFileWatch', id),
  setTitleBarTheme: payload => ipcRenderer.send('plobi:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('plobi:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('plobi:translucency', payload),
  setPreviewShortcutActive: active => ipcRenderer.send('plobi:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('plobi:openExternal', url),
  openPreviewInBrowser: url => ipcRenderer.invoke('plobi:openPreviewInBrowser', url),
  fetchLinkTitle: url => ipcRenderer.invoke('plobi:fetchLinkTitle', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('plobi:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('plobi:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('plobi:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('plobi:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('plobi:zoom:get'),
    setPercent: percent => ipcRenderer.send('plobi:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('plobi:zoom:changed', listener)

      return () => ipcRenderer.removeListener('plobi:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('plobi:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('plobi:logs:recent'),
  readDir: dirPath => ipcRenderer.invoke('plobi:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('plobi:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('plobi:fs:reveal', targetPath),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('plobi:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('plobi:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('plobi:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('plobi:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('plobi:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('plobi:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('plobi:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('plobi:git:branchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('plobi:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('plobi:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('plobi:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('plobi:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('plobi:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('plobi:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('plobi:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('plobi:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('plobi:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('plobi:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('plobi:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('plobi:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('plobi:git:review:shipInfo', repoPath),
      createPr: repoPath => ipcRenderer.invoke('plobi:git:review:createPr', repoPath)
    }
  },
  terminal: {
    dispose: id => ipcRenderer.invoke('plobi:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('plobi:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('plobi:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('plobi:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `plobi:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `plobi:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('plobi:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('plobi:close-preview-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('plobi:open-updates', listener)

    return () => ipcRenderer.removeListener('plobi:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:deep-link', listener)

    return () => ipcRenderer.removeListener('plobi:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('plobi:deep-link-ready'),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:window-state-changed', listener)

    return () => ipcRenderer.removeListener('plobi:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('plobi:focus-session', listener)

    return () => ipcRenderer.removeListener('plobi:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:notification-action', listener)

    return () => ipcRenderer.removeListener('plobi:notification-action', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('plobi:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:backend-exit', listener)

    return () => ipcRenderer.removeListener('plobi:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('plobi:connection:applied', listener)

    return () => ipcRenderer.removeListener('plobi:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('plobi:power-resume', listener)

    return () => ipcRenderer.removeListener('plobi:power-resume', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:boot-progress', listener)

    return () => ipcRenderer.removeListener('plobi:boot-progress', listener)
  },
  // Local quota hub catalog, pushed by the main process once the hub is live.
  // The renderer subscribes so apps the hub serves connect themselves — no manual
  // click, and no second catalog copied into the UI (it renders what the hub says).
  onQuotaApps: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:quota-apps', listener)

    return () => ipcRenderer.removeListener('plobi:quota-apps', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('plobi:bootstrap:get'),
  resetBootstrap: () => ipcRenderer.invoke('plobi:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('plobi:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('plobi:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('plobi:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('plobi:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('plobi:version'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('plobi:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('plobi:uninstall:summary'),
    run: mode => ipcRenderer.invoke('plobi:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('plobi:updates:check'),
    apply: opts => ipcRenderer.invoke('plobi:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('plobi:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('plobi:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('plobi:updates:progress', listener)

      return () => ipcRenderer.removeListener('plobi:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('plobi:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('plobi:vscode-theme:search', query)
  },
  // Plobi Gateway (aigw) — local desktop-quota aggregator. Lets the renderer
  // spawn / stop / query the user's own aigw OpenAI-compatible gateway.
  plobiGateway: {
    start: () => ipcRenderer.invoke('plobi-gateway:start'),
    stop: () => ipcRenderer.invoke('plobi-gateway:stop'),
    status: () => ipcRenderer.invoke('plobi-gateway:status'),
    auth: (appId: string) => ipcRenderer.invoke('plobi-gateway:auth', appId)
  }
})
