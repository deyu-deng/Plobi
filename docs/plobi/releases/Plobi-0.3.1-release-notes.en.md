# Plobi v0.3.1

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.1) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.1/Plobi-0.3.1-release-notes.en.md)

Previous release: [v0.3.0](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.0)

Against v0.3.0, three things change in ways you can notice directly: the quota adapter no longer moves with the client, the note sidebar goes from absent to five panels, and the faults v0.3.0 left in the data directory, self-update and CJK name lookup are fixed. Each entry below states the difference from the previous release.

Installers are attached below.

### New

- **The quota adapter updates on its own** — In the previous release the adapter shipped bound to the client, so changing the adapter meant reinstalling the client. The adapter now checks for a new version, downloads and verifies it, then swaps it in; if the swapped build fails to start, the bundled copy takes over. The entry point is the quota menu in the status bar, and nothing is downloaded until you use it.
- **Import of local account configuration** — The previous release had no such entry. The setup wizard and the settings page now show the sign-in state of tools already present on the machine and let you decide whether to import it; the import writes a copy, and the original tool stays signed in.
- **Secondary agents resume their own session** — The previous release opened a new session every time you entered a secondary agent, dropping whatever it had said in the last exchange. The agent's own session is now continued.
- **Per-agent model selection** — The previous release shared one model setting across the whole machine. The Models page now assigns a model to an individual agent, written to that agent's own entry.
- **Note sidebar** — The previous release had no sidebar on notes. Backlinks, outgoing links, tags, properties and bookmarks are now present.

### Improved

- **Locally built installers carry offline dependencies too** — The previous release's "no network on first launch" held only for installers built in CI; a locally built one still resolved dependencies from PyPI. Locally built installers now carry them as well.
- **Toolchain staged before packaging** — In the previous release a flaky network stalled the build or repeated downloads. Git, Node, Python, uv, ffmpeg and ripgrep are now staged before packaging, and parts already downloaded are reused.
- **Build junk removed before packaging** — The previous release carried stale hashes in `dist`, `__pycache__` and `.map` files into the package. They are dropped before packaging, and the new `npm run check:payload` verifies payload size and lists the entries over budget.

### Fixed

- **Data directory record survives uninstall** — The previous release dropped the recorded data directory path on uninstall, so a reinstall returned to the default location on the system disk and data on another partition appeared to be gone. The record is now kept.
- **Startup failure when the data directory is unavailable** — When the data directory sat on a removable or secondary disk that was not mounted, the previous release created a directory at a path that did not exist and ended with `ENOENT`. It now falls through to the next usable directory and records the landing spot and the reason in the startup log.
- **Repeated installer downloads on self-update** — The previous release downloaded about 400 MB from the start on every update. A completed download is now reused, an interrupted one resumes from the bytes already present, and the official size is checked at the end; a package of the wrong size is not handed to the installer.
- **Agent names with full-width characters could not be found** — The previous release normalised only the roster side and left the query string as given, so names containing full-width Roman numerals and similar characters never matched. Both sides now use the same normalisation.
- **Stale adapter versions left on disk** — The previous release left the previous adapter copy behind on every swap, at about 74 MB each. The previous version is now removed.
- **Location of adapter downloads** — The previous release downloaded adapter files inside the configuration directory, so backing up or migrating that directory carried them along. They now live outside it.
- **Adapter handover after an upgrade** — In the previous release an existing adapter could be shadowed by an older build after the client upgraded. An upgrade now takes over the existing adapter correctly, and a bundled version newer than the downloaded one is no longer shadowed.
- **Source of identity data** — The previous release read identity, descriptions and row-level filters from a per-agent home directory that is no longer used. The entry in the roster is read instead.

### Install

Run the installer attached below — `.exe` (choose the data directory) or `.msi` (default location) on Windows, `.dmg` on macOS, and `.deb` / `.rpm` / `.AppImage` on Linux.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.0...v0.3.1)
