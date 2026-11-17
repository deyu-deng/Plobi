# Plobi v0.3.1

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.1) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.1/Plobi-0.3.1-release-notes.en.md)

v0.3.1 gives the desktop app subscription quotas kept by one maintainer, a full note sidebar, resumable sessions for secondary agents, and a model chosen per agent.

Installers are attached below.

### New

- **Subscription quotas under one maintainer** — Sign-in, the model catalogue and quota queries for every provider are maintained by magpie; it ships with the installer and runs at startup, so the sign-in entries and the provider roster arrive with it.
- **Card list follows the roster** — When magpie adds, removes or renames a provider, the Plobi side changes one cell of a mapping table: the table decides whether a card appears, its name and its sign-in method, while published model ids and presets stay as they are.
- **Import of a local quota configuration** — The settings page and the setup wizard list the quota configuration already present on the machine and leave the decision to import it to the user; the import writes a copy, and the original tool stays signed in.
- **Secondary agents resume their own session** — Entering a secondary agent continues that agent's own session, so what it said in the last exchange is kept.
- **Per-agent model selection** — The Models page assigns a model to an individual agent, and the choice is written to that agent's own entry.
- **Note sidebar** — The previous release had no sidebar on notes; this release adds backlinks, outgoing links, tags, properties and bookmarks.

### Improved

- **The quota layer updates on its own** — The quota layer can be updated without waiting for a new application release; the copy shipped in the installer serves as the floor.
- **Sign-in entries arrive with the installer** — The sign-in items and the provider roster ship with the installer, so a fresh machine lists the sign-in entries instead of leaving them blank.
- **Usage reporting off by default** — magpie reports usage outward once a day; from this version on, that report is off unless turned on.

### Fixed

- **Models that could not be switched in the picker** — Switching a model in the picker always answered "Unknown provider"; models can be switched now.
- **Sign-in from the `+` control did nothing** — Sign-in needs a terminal, and an interface that was not mounted left a blank page; the sign-in command now runs in a terminal, the browser is opened, and an interface that fails to mount is reloaded.
- **The quota screen stayed on its loading state** — That screen's data fetch sat behind another screen's, so a slow credential check left the whole screen as a single spinner; the two screens no longer block each other.
- **Card icons falling back to initials** — Only three of 22 cards kept an icon; the icons are restored from the new roster: 12 brand-coloured marks, five official marks, and initials for the three providers without a public mark.
- **Free pools shown as configured** — The OpenCode free pool appeared usable without a key; that leg takes a paid key only and reports a missing credential otherwise.
- **Two Marvis cards shown without a working path** — Neither of the two cards starts on this machine; both are out of the card list, and their entries are off by default in the settings.
- **Stale copies left on disk** — Every change of the quota layer left the previous copy behind, about 74 MB each; the previous copy is removed on replacement now.
- **Downloads travelling with the settings** — The quota layer's downloads sat inside the configuration directory, so backing up or migrating that directory carried them along; they live outside it now.
- **Handover after an upgrade** — After the client upgraded, an existing copy could be shadowed by an older build; the handover is correct now, and a bundled build newer than the downloaded one is no longer shadowed.
- **The data directory record survives uninstall** — The previous release dropped the recorded data directory path on uninstall, so a reinstall returned to the default location on the system disk; the record is kept now.
- **Startup failure when the data directory is unavailable** — When the data directory sat on a removable or secondary disk that was not mounted, startup ended on a path that did not exist; it falls through to the next usable directory now and records the landing spot and the reason in the startup log.
- **Repeated installer downloads on self-update** — Every update downloaded about 400 MB from the start; a completed download is reused now, an interrupted one resumes from the bytes already present, and the official size is checked at the end, so a package of the wrong size is not handed to the installer.
- **Agent names with full-width characters could not be found** — Only the roster side was normalised and the query string was left as given, so names containing full-width Roman numerals never matched; both sides use the same normalisation now.
- **Agent identity and descriptions read from the roster** — Identity and descriptions came from a per-agent directory that is no longer used; the entry in the roster is read instead.

### Install

Download the installer below and run it — Windows uses `.exe` (lets you pick the data directory) or `.msi` (uses the default location), macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.0...v0.3.1)
