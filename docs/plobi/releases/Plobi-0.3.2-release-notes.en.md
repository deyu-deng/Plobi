# Plobi v0.3.2

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.2) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.2/Plobi-0.3.2-release-notes.en.md)

v0.3.2 adds dispatch and authorization to the desktop app: a decision stated in conversation becomes an authorization, and dispatched work carries a ticket that can be looked up, revoked and reported on time; external tools get one catalog holding their accounts and their setup guidance.

Installers are attached below.

### New

- **Authorization given in conversation** — A decision stated in conversation becomes an authorization, and that sentence itself is kept as the record; such authorizations sit in a ledger, can be looked up and can be withdrawn. Approve, reject and withdraw are explicit decisions, and a draft proposed by an Agent has no effect until a person approves it.
- **Tickets for dispatched work** — Dispatched work carries a ticket showing its current state, who it went to, and which domain it belongs to; "Revoke" stops it.
- **Scheduled reporting** — A scheduled job reports the outcome it reached when it fires; out of the box it asks first.
- **One catalog for external tools** — 50 vendors each occupy one row of a single table, and adding one changes one place.
- **Accounts and legs per vendor** — Which accounts a family holds and which one dispatch uses; a vendor can carry more than one leg, and dispatch can name the leg to take.
- **Setup guidance for tools not yet installed** — 48 vendors carry download and sign-in steps, shown the first time such a tool is added.
- **Session access for more external tools** — Sessions of Gemini CLI, Kilo Code, Cline, kiro, GitHub Copilot and Qoder China can be read and answered.
- **Fine-grained actions on notes, diary and dossiers** — List the whole library, read a link, change a property, tick one item, change one stage.
- **Source management** — Sources can be listed, read, switched on and off, put on another cadence, configured, and a collection round can be triggered directly.
- **Agent capabilities can be asked about and configured** — One place answers what an Agent can call now; the toolbox and blacklist an expert carries are editable in the capabilities panel.

### Improved

- **One set of role names** — Across four languages the top level is called the Director, the second level the Chief, and the other two roles the Expert and the Guest; the old names no longer reach the screen.
- **The interface no longer rebuilds itself** — A whole transcript is no longer torn down and remounted every 2.5 seconds, and scrolling a note page no longer rebuilds the Markdown as a whole tree.
- **Loading state** — The brand turtle walks left to right along the progress bar; switching screens shows the loading screen on its own.
- **Dossier card folds as its container narrows** — The column at the top right of the chat card holds dossiers and pending approvals in one shell; when it narrows, that column folds down to the icon.
- **Browser tools folded into one** — The dozen click-style tools become a single one, and an Agent no longer lists them one by one.
- **Model names on screen** — Model names lose the provider prefix and read as the model's own name.
- **Default guest for a chief's call** — The default is now the one that actually runs on this machine.
- **Titles in the left rail** — A name the vendor chose comes first; only when none can be read is the wrapper stripped to plain words.
- **Transcript follows a runtime switch** — Rebinding the runtime of the same conversation carries the on-screen transcript along.

### Fixed

- **Bundled backend that could not start** — The bundled backend used to fail to start after an install; it starts now.
- **First launch and updates** — The first-launch marker repairs itself; a managed install completes a shallow history first, and updates are handled per installation method.
- **One version number on the About page** — Several version numbers used to be printed there; exactly one is printed now.
- **App icons** — Each platform gets an icon of its own, and the menu bar icon follows the system appearance.
- **Quota tool** — That leg calls itself WorkBuddy now and no longer CodeBuddy; its self-update download resumes from the bytes already fetched, a stalled transfer is detected, and three rounds of retry are made.
- **Collection fault reported while the WeChat source was off** — A source that is not enabled no longer reports a collection fault.
- **Engine settings** — That control is disabled while the engine is off, and saving no longer loses the engine section.
- **Messages dropped silently by a local gate** — The refusal carries a reason, and the refused message goes into the queue.
- **Sending bucketed per Agent** — Each Agent has its own busy state, queue and send lock, and none of them is taken by another.
- **Where notes, diary and dossiers live** — Notes, diary and dossiers no longer sit one directory deeper; they land in the data directory directly.

### Install

Download the installer below and run it — Windows uses `.exe` (lets you pick the data directory) or `.msi` (uses the default location), macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.1...v0.3.2)
