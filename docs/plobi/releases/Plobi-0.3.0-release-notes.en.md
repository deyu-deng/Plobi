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
- **In-app update** — The desktop app can download a new version, install it, bring the backend to the same version and restart.

### Improved

- **Separate version numbers** — The first line is the desktop version and the second is the backend version; the two no longer share one number.
- **Backend ships with the installer** — Installing no longer downloads dependencies separately, and the shell and the backend end up on the same version.
- **One name for agents** — "Profile" is gone from the product; the left rail, the settings page and the command line all use Agent.
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

Download the installer below and run it — Windows uses `.exe` or `.msi`, macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.0...main)
