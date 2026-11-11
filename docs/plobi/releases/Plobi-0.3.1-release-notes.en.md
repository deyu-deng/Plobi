# Plobi v0.3.1

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.1) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.1/Plobi-0.3.1-release-notes.en.md)

v0.3.1 ships the quota adapter separately from the client, completes the note sidebar and per-agent model selection, and fixes a set of faults in the data directory, self-update and identity lookup.

Installers are attached below.

### New

- **Separately distributed quota adapter** — magpie no longer ships with the client. The adapter checks for a new version, downloads and verifies it, then swaps it in; if the swapped build fails to start, the bundled copy takes over. The entry point is the quota menu in the status bar, and nothing is downloaded while it is unused.
- **Import of local account configuration** — The setup wizard and the settings page list the sign-in state of tools already present on the machine and offer to import it. The import writes a copy, and the original tool stays signed in.
- **Resumed sessions in secondary agents** — Entering a secondary agent continues its own session; previously a new one was created each time and the previous exchange was dropped.
- **Per-agent model selection** — The Models page assigns a model to an individual agent. The choice is written to that agent's entry in the roster instead of applying to the whole machine.
- **Note sidebar** — Backlinks, outgoing links, tags, properties and bookmarks are now present.

### Improved

- **Offline dependencies in locally built installers** — A locally built installer still resolved dependencies from PyPI on first launch. It now carries them, so the first launch needs no network.
- **Toolchain prepared before packaging** — Git, Node, Python, uv, ffmpeg and ripgrep are staged before packaging, and parts already downloaded are reused, so a flaky network no longer stalls or repeats a download.
- **Build junk removed before packaging** — Stale hashes in `dist`, `__pycache__` and `.map` files are dropped before packaging. A new `npm run check:payload` verifies payload size and lists the entries over budget.

### Fixed

- **Data directory record survives uninstall** — Uninstalling used to drop the recorded data directory path, so a reinstall returned to the default location on the system disk and data on another partition appeared to be gone. The record is now kept.
- **Startup failure when the data directory is unavailable** — When the data directory sat on a removable or secondary disk that was not mounted, startup created a directory at a path that did not exist and ended with `ENOENT`. It now falls through to the next usable directory and records the landing spot and the reason in the startup log.
- **Repeated installer downloads on self-update** — Each update used to download about 400 MB from the start. A completed download is now reused, an interrupted one resumes from the bytes already present, and the official size is checked at the end; a package of the wrong size is not handed to the installer.
- **Agent names with full-width characters could not be found** — Identity lookup normalised only the roster side and left the query string as given, so names containing full-width Roman numerals and similar characters never matched. Both sides now use the same normalisation.
- **Stale adapter versions left on disk** — Every adapter swap left the previous copy behind at about 74 MB each. The previous version is now removed.
- **Location of adapter downloads** — Files downloaded for the adapter are moved out of the configuration directory, so backing up or migrating that directory no longer carries them along.
- **Adapter handover after an upgrade** — An upgrade now takes over the existing adapter correctly, and a bundled version newer than the downloaded one is no longer shadowed by the older build.
- **Source of identity data** — Identity, descriptions and row-level filters no longer depend on a per-agent home directory; the entry in the roster is read instead.

### Install

Run the installer attached below — `.exe` (choose the data directory) or `.msi` (default location) on Windows, `.dmg` on macOS, and `.deb` / `.rpm` / `.AppImage` on Linux.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.0...v0.3.1)
