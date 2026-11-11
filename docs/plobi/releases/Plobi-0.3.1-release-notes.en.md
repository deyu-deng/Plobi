---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 8eedff1fe7e84c1d5b90a145153aac3c_544bad21ddc311f1bc42525400638852
    ReservedCode1: USvs+TliPzY5BEJ2lK0I8cVu6QSuRMgU0aMFuHquf0tX8wglWwPw0cLx+4DJEpSN5CPgC5OxQn3lNK7/AksqKUhpHIVolRPhpLC1am9508Zh0MXarGDxC6KLrJy5vCY2ypIHizOeku7SwT4MrXGO3oWVgOt0qJLQ9Lntiuz0P1Cn+y50/Vmukve4V9k=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 8eedff1fe7e84c1d5b90a145153aac3c_544bad21ddc311f1bc42525400638852
    ReservedCode2: USvs+TliPzY5BEJ2lK0I8cVu6QSuRMgU0aMFuHquf0tX8wglWwPw0cLx+4DJEpSN5CPgC5OxQn3lNK7/AksqKUhpHIVolRPhpLC1am9508Zh0MXarGDxC6KLrJy5vCY2ypIHizOeku7SwT4MrXGO3oWVgOt0qJLQ9Lntiuz0P1Cn+y50/Vmukve4V9k=
---

# Plobi v0.3.1

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.1) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.1/Plobi-0.3.1-release-notes.en.md)

Previous release: [v0.3.0](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.0)

Against v0.3.0, this release replaces the substrate under subscription quotas: sign-in, model catalogue and quota queries for every provider are now maintained by magpie, Plobi no longer writes its own connector per provider and only adapts the display on top of magpie's list. Two further changes are visible directly -- the note sidebar goes from absent to five panels, and the faults v0.3.0 left in the data directory, self-update and CJK name lookup are fixed. Each entry below states the difference from the previous release.

Installers are attached below.

### New

- **Subscription quotas maintained by magpie** — In the previous release each provider had its own connector written here, so a change on the provider side meant a client change. magpie now ships with the installer and runs at startup, maintaining sign-in, the model catalogue and quota queries for every provider; a provider added there needs no new connector code in the client.
- **Card list follows magpie's provider roster** — In the previous release the card list was fixed in the client, so any addition or removal on the magpie side required a client change. The list now reads the roster magpie writes out (22 providers), while whether a card appears, its name and its sign-in method come from a mapping table kept on the Plobi side: a rename upstream changes one cell in that table, and the published model ids and presets stay as they are.
- **Import of a local magpie configuration** — The previous release had no such entry. The settings page and the setup wizard list the magpie configuration already present on the machine and leave the decision to import it to the user; the import writes a copy, and the original tool stays signed in.
- **Secondary agents resume their own session** — The previous release opened a new session every time a secondary agent was entered, dropping whatever it had said in the last exchange. The agent's own session is now continued.
- **Per-agent model selection** — The previous release shared one model setting across the whole machine. The Models page now assigns a model to an individual agent, written to that agent's own entry.
- **Note sidebar** — The previous release had no sidebar on notes. Backlinks, outgoing links, tags, properties and bookmarks are now present.

### Improved

- **magpie updates independently of the client** — In the previous release the quota adaptation layer was bound to the client, so several magpie releases in one night could only be picked up by reinstalling. A bundled copy now serves as the floor, a writable copy is kept alongside and resolved first, and its digest comes from the upstream `SHA256SUMS`; a build that fails to start is recorded and skipped, falling back to the bundled copy.
- **Bundled plugin set** — magpie installs no plugins by itself, so on an empty data directory its provider roster is empty and the sign-in entry has no name to act on. The installer now carries a plugin list, the roster (22 rows, sign-in state fields removed) and 21 plugin packages, each checked against its `sha256`.
- **Separate port and configuration directory for magpie** — Port, configuration directory and logs are separate from the client, with configuration written under the data directory; the port is probed before startup and an existing process record is taken over when another instance is detected.
- **Usage reporting disabled** — magpie reports usage to `usemagpie.ai` once a day by default; `DO_NOT_TRACK=1` disables that report in this release.
- **Locally built installers carry offline dependencies too** — The previous release's "no network on first launch" held only for installers built in CI; a locally built one still resolved dependencies from PyPI. Locally built installers now carry them as well.
- **Toolchain staged before packaging** — In the previous release a flaky network stalled the build or repeated downloads. Git, Node, Python, uv, ffmpeg and ripgrep are now staged before packaging, and parts already downloaded are reused.
- **Build junk removed before packaging** — The previous release carried stale hashes in `dist`, `__pycache__` and `.map` files into the package. They are dropped before packaging, and the new `npm run check:payload` verifies payload size and lists the entries over budget.

### Fixed

- **Models that could not be switched to in the picker** — The previous release had two provider resolvers in the backend: the credential side knew the gateway leg and the switching side did not, so its models answered `Unknown provider`. The plugin registry now backs both as a third source, and the two directions give the same answer for the same name.
- **Sign-in that never started from the `+` control** — The previous release sent sign-in through Plobi's own channel, while magpie signs in through a terminal; a renderer that was alive but not mounted left a blank page. `magpie plugin login` now runs in a terminal and magpie opens the browser, and a renderer that is alive but not mounted is reloaded by the main process.
- **A quota screen that stayed on its loading state** — In the previous release that screen's branch sat behind the data fetch for the API-keys screen, so a slow credential check left the whole screen as a single spinner. The branch now sits ahead of it, and the two screens no longer block each other.
- **Card icons falling back to initials** — The previous release looked icons up by the old leg names, so once quota leg ids became magpie provider ids only three of 22 cards kept an icon. Keys now follow the new ids: 12 brand-coloured marks from simple-icons, five official marks, and initials kept for the three providers without a public vector mark.
- **Free pools shown as configured** — In the previous release the OpenCode free pool appeared usable without a key. With the free pool owned by magpie, that leg accepts a paid key only and reports a missing credential otherwise.
- **Two Marvis cards shown without a working path** — The previous release kept `marvis_cli` and `marvis_gui` cards in the adaptation table, and neither starts on this machine. Both are out of the card list, and their legs are off by default in configuration.
- **Stale magpie copies left on disk** — The previous release left the previous copy behind on every version change, at about 74 MB each. The previous version is now removed.
- **Location of magpie downloads** — The previous release downloaded magpie files inside the configuration directory, so backing up or migrating that directory carried them along. They now live outside it.
- **magpie handover after an upgrade** — In the previous release an existing copy could be shadowed by an older build after the client upgraded. An upgrade now takes over the existing copy correctly, and a bundled version newer than the downloaded one is no longer shadowed.
- **Data directory record survives uninstall** — The previous release dropped the recorded data directory path on uninstall, so a reinstall returned to the default location on the system disk and data on another partition appeared to be gone. The record is now kept.
- **Startup failure when the data directory is unavailable** — When the data directory sat on a removable or secondary disk that was not mounted, the previous release created a directory at a path that did not exist and ended with `ENOENT`. It now falls through to the next usable directory and records the landing spot and the reason in the startup log.
- **Repeated installer downloads on self-update** — The previous release downloaded about 400 MB from the start on every update. A completed download is now reused, an interrupted one resumes from the bytes already present, and the official size is checked at the end; a package of the wrong size is not handed to the installer.
- **Agent names with full-width characters could not be found** — The previous release normalised only the roster side and left the query string as given, so names containing full-width Roman numerals and similar characters never matched. Both sides now use the same normalisation.
- **Source of identity data** — The previous release read identity, descriptions and row-level filters from a per-agent home directory that is no longer used. The entry in the roster is read instead.

### Install

Run the installer attached below -- `.exe` (choose the data directory) or `.msi` (default location) on Windows, `.dmg` on macOS, and `.deb` / `.rpm` / `.AppImage` on Linux.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.0...v0.3.1)
*（内容由AI生成，仅供参考）*
