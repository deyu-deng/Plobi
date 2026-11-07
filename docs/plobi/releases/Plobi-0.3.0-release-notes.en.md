# Plobi v0.3.0

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.0) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.0/Plobi-0.3.0-release-notes.en.md)

v0.3.0 adds three areas to the desktop app — Knowledge, Diary and Workbench — and brings in-app updates and access to AI tools outside Plobi.

Installers are attached below.

### New

- **Knowledge** — The left rail gains a Knowledge entry. Notes can be searched, outlined and edited in place; `[[wikilinks]]` open on click and preview the target note on hover.
- **Diary** — The left rail gains a Diary entry. Entries are written and read day by day, and new text is filed under the current day immediately.
- **Workbench tabs** — The right rail becomes a set of tabs holding the agenda, workspace files, dossiers, review, a built-in browser and a terminal.
- **Office document editing** — Word, Excel and PowerPoint files can be edited inside the application.
- **PDF annotations** — Highlights and annotations are supported, and annotations are written back to the file.
- **Memory confirmation layer** — A memory write becomes a pending entry first, approved or rejected one by one; the settings page lists the same entries and can forget one.
- **External AI tools** — Claude Code, Codex, Gemini CLI, Qoder, Cline, OpenCode, Antigravity and Tencent's coding agent are supported. Each entry shows its install state, sign-in state and whether it can resume a session, and its conversations can be read and answered.
- **Expert roles** — Four roles can be dispatched: file, browser, software and computer. The left rail shows the log of each one.
- **Agenda checklists and attachments** — Agenda entries can carry a checklist and attachments.
- **New sources** — Mailboxes (QQ, 163, Gmail, Outlook and self-hosted IMAP), Mubu, DingTalk, undergraduate course affairs and timetables.
- **In-app update** — A new version is announced and can be installed directly: the shell is replaced first, then the backend is installed from the same package. No git, no Python environment and no manual steps are required, and the shell and the backend end on the same version.
- **Data directory choice** — The installer can ask for the folder that holds sessions, settings and memory; that folder is the one that gets used.

### Improved

- **Separate version numbers** — The first line is the desktop version and the second is the backend version; the two no longer share one number.
- **Backend ships with the installer** — The backend's runtime source and its locked dependency wheels come with the package, so its dependencies install from the package; the shell and the backend end up on the same version.
- **Toolchain ships with the installer** — git, bash and ripgrep come with the package, so the first launch no longer downloads them.
- **One name for agents** — The interface says Agent and nothing else: "profile" is gone from the product, and the left rail, the settings page and the command line all use Agent.
- **A fresh install has one agent** — The previous version added an agenda secretary on its own; a fresh install now has the chief secretary only. Roles are created by the user, or by the chief secretary when the user asks for one.
- **No preset scheduled jobs** — The previous version scheduled a nightly plan for 20:00 on its own; this version presets none, and scheduled jobs are set by the user.
- **Three permission levels** — Ask every time, auto-approve and full access, switched next to the input box.
- **Status text carries a next step** — Not installed, not signed in, no path configured, file missing and service not listening each come with a matching instruction.
- **Card layout** — The conversation and the work surface appear as two cards that can be dragged into place, or one of them can be shown alone.
- **Quota listed per vendor** — Claude Code, Codex, OpenCode, Gemini, Kiro, Cline, Antigravity, Qoder and Cursor each get a card showing the sign-in state.
- **Collection frequency control** — Collection frequency is a dial, and any single chat source can be switched off.

### Fixed

- **First launch reinstall loop** — A probe timeout used to be read as a broken environment; it now only means a slow response, and first launch no longer falls into a reinstall loop.
- **Managed installs could not update** — A shallow clone used to be read as a local divergence, which stopped every managed install from updating; managed installs can update again.
- **Conclusion of a failed update check** — A rate-limited or malformed response no longer reports that the installation is up to date, and reports the failure instead.
- **New version notice** — The notice stays until it is dismissed, and an unacknowledged notice appears again on the next launch.
- **DingTalk collection window** — Every round used to return the same oldest batch, so new messages never arrived; the newest batch is taken now.
- **Images in Word documents** — Non-standard prefixes are rewritten to the standard form before parsing, and images render.

### Install

Download the installer below and run it — Windows uses `.exe` (lets you pick the data directory) or `.msi` (uses the default location), macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.0...main)
