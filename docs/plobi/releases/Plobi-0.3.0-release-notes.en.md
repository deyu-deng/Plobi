# Plobi v0.3.0

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.0) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.0/Plobi-0.3.0-release-notes.en.md)

This release puts three new places in the desktop app: Knowledge, Diary and the Workbench. It also lets you update the whole app with one click, and hand work to an AI app outside Plobi.

The installer is below.

### New

- **The left rail has a Knowledge base** — Notes can be searched, outlined and edited; `[[wikilinks]]` open, and hovering one previews the note.
- **There is a Diary** — Write by day, flip through days; jot one line and it lands in today's entry right away.
- **The right rail is a row of tabs** — Agenda, workspace files, dossier, review, a built-in browser and a terminal. Opening a page no longer means leaving the app.
- **Word, Excel and PowerPoint can be edited in place** — PDF can be highlighted and annotated, and the annotations go back into the file.
- **Memory asks before it writes** — Entries that would go into the brain wait as to-dos; approve or reject them one by one. Settings shows the same list, and lets you forget an entry.
- **Work can be handed to an AI app outside Plobi** — Claude Code, Codex, Gemini CLI, Qoder, Cline, OpenCode, Antigravity, Tencent's coding agent. For each one you can see whether it is installed, signed in, and able to resume a session; you can read its conversations and talk back to it.
- **Four kinds of expert can be sent out** — File, Browser, Software, Computer. The left rail shows what each one has been doing.
- **Agenda items can carry a checklist and attachments** — and new sources feed them: mailboxes (QQ, 163, Gmail, Outlook, your own IMAP), Mubu, DingTalk, undergraduate course affairs, timetables.
- **The desktop app can update itself in one click** — A new version says so; one click downloads it, installs it, brings the backend to the same version, and restarts.

### Improved

- **Versions no longer get mixed up** — The first line is this program's version, the second is the backend's.
- **The install is the whole thing** — The backend travels inside the installer: nothing to download separately, and no waiting on the network for dependencies. The shell and the backend end up on the same version.
- **"Profile" is gone from the product** — An Agent is one row in the roster; the left rail, the settings page and the command line all call it an Agent now.
- **Permissions have three levels** — Ask every time, auto-approve, or full access, switched next to the input box.
- **Every status word says what to do next** — Not installed, not signed in, no path set, file missing, service not answering: each one comes with a sentence that tells you what to do.
- **The main window can be card-style** — The conversation and the work surface are two cards you can drag into place, or show one of.
- **Quota reads are listed per vendor** — One card per app installed on this machine, with its sign-in state; Claude Code, Codex, OpenCode, Gemini, Kiro, Cline, Antigravity, Qoder and Cursor are covered.
- **Collection frequency is a dial** — You can also switch off any single chat source.

### Fixed

- **First launch no longer falls into that reinstall loop** — A probe timeout used to be read as "the environment is broken"; now it only means slow.
- **Managed installs can update again** — A shallow clone used to read "behind" as "diverged", so no managed install could ever update.
- **A failed update check no longer claims you are up to date** — Rate-limited or malformed responses now say so.
- **The new-version notice stays until you dismiss it** — If you did not see it, it comes back on the next launch.
- **The DingTalk collector takes the newest messages** — It used to hand over the same oldest batch every round, so new messages never arrived.
- **Images in Word documents show up** — Non-standard prefixes are rewritten to the usual form before parsing.

### Install

Download the installer below — Windows uses `.exe` or `.msi`, macOS uses `.dmg`, Linux uses `.deb` / `.rpm` / `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.0...main)
