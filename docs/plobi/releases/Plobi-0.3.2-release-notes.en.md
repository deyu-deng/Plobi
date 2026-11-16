# Plobi v0.3.2

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.2) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.2/Plobi-0.3.2-release-notes.en.md)

Previous release: [v0.3.1](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.1)

Against v0.3.1, this release makes dispatch and authorization directly usable: a decision stated in conversation becomes an authorization, and such authorizations can be looked up and withdrawn; dispatched work carries a ticket that can be looked up, revoked and reported on time. External tools collapse into one source table, where adding one vendor changes one place. Three sources of lag and remounting are gone from the interface: a transcript is no longer torn down and remounted every 2.5 seconds, scrolling a note page no longer rebuilds the whole tree, and the loading state is a brand turtle walking along the progress bar.

Installers are attached below.

### New

- **Authorization given in conversation** — A decision stated in conversation becomes an authorization, and that sentence itself is kept as the record; such authorizations sit in a ledger, can be looked up, and can be withdrawn.
- **Three decisions on an authorization** — Approve, reject, withdraw. A draft proposed by an Agent has no effect until a person approves it.
- **Tickets for dispatched work** — Dispatched work carries a ticket showing its current state, who it went to, and which domain it belongs to; "Revoke" stops it.
- **Scheduled reporting** — A scheduled job reports its outcome when it fires; the criteria ships switched off and asks first.
- **One source table for external tools** — 50 vendors each occupy one row, and adding one changes one place; the four exit tables (contract, setup guidance, session, account) are all derived from it.
- **Accounts and legs per vendor** — Which identities a family holds and which one dispatch uses; a vendor can carry more than one leg, and dispatch can name the leg to take.
- **Setup guidance for tools not yet installed** — 48 vendors carry download and sign-in steps, shown the first time such a tool is added.
- **Session access for more external tools** — Sessions of Gemini CLI, Kilo Code, Cline, kiro, GitHub Copilot and Qoder China can be read and answered.
- **Fine-grained tools for notes, diary and dossiers** — List the whole library, read a link, change a property, tick one item, change one stage.
- **Six source actions** — `source_list`, `source_read`, `source_enable`, `source_set_cadence`, `source_configure`, `source_collect`: see, read, toggle, change the cadence, write the configuration, trigger a collection round.
- **Agent capabilities can be asked about and configured** — One place answers what an Agent can call now; the toolbox and blacklist an expert carries are editable in the capabilities panel.

### Improved

- **One set of role names across four languages** — The top level is called the Director, the second level the Chief, and the other two roles the Expert and the Guest; the old names no longer reach the screen.
- **Transcript no longer remounts on a timer** — The message component keys on content, so a transcript is no longer torn down and remounted every 2.5 seconds.
- **Loading state** — The brand turtle walks left to right along the progress bar; switching screens shows the loading screen on its own.
- **Note page scrolling** — Scrolling walks a rAF binary search, and Markdown is no longer rebuilt as a whole tree.
- **Dossier card folds as its container narrows** — The column at the top right of the chat card holds dossiers and pending approvals in one shell; when it narrows, that column folds down to the icon.
- **Browser tools folded into one** — The dozen click-style tools stop being registered; the category becomes `browser_exec`.
- **Gateway directory renamed** — `quota_hub` becomes `bezel`, and model names on screen lose the upstream prefix.
- **Default guest for a chief's call** — The default is now the one that actually runs on this machine.
- **Titles in the left rail** — A name the vendor chose comes first; only when none can be read is the wrapper stripped to plain words.
- **Transcript follows a runtime switch** — Rebinding the runtime of the same conversation carries the on-screen transcript along.

### Fixed

- **Bundled backend that could not start** — The bundled backend was dead code: the bundled check was missing, the build left packages out, and downloads had no fallback; all three are fixed and the bundled backend now starts.
- **First-launch marker** — The bootstrap marker repairs itself, and updates are split by the way the app was installed.
- **Shallow clone during install** — The clone is staged per ref, and a shallow history is completed first.
- **One version number on the About page** — Several version numbers used to be printed there; exactly one is printed now.
- **Icons** — Each platform gets an icon of its own, and the menu bar uses a template image.
- **Quota leg renamed** — That leg calls itself WorkBuddy now and no longer CodeBuddy.
- **Quota self-update download** — The transfer resumes from the bytes already fetched, a stalled transfer is detected, and three rounds of retry are made.
- **Collection fault reported while the WeChat source was off** — A source that is not enabled no longer reports a collection fault.
- **System proxy takeover clickable with the engine off** — That control is disabled while the engine is off.
- **Save dropping the engine section** — Saving no longer loses the engine section.
- **Messages dropped silently by a local gate** — The refusal carries a reason, and the refused message goes into the queue.
- **Sending bucketed per Agent** — The busy semantics, the queue bucket and the send lock all belong to one Agent.
- **Where the human-readable surface lives** — It moves from `plobi/mind/` to `plobi/human/`, and the extra directory level is gone.

### Install

Run the installer attached below -- `.exe` (choose the data directory) or `.msi` (default location) on Windows, `.dmg` on macOS, and `.deb` / `.rpm` / `.AppImage` on Linux.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.1...v0.3.2)
