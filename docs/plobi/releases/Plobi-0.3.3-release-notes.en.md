## Plobi v0.3.3

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.3) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.3/Plobi-0.3.3-release-notes.en.md)

v0.3.3 rebuilds the ten first-run steps into a set of glass cards, lets a selection be edited in place, and settles memory and dossiers into one row per item. Every collection leg now carries its own identity and true names, and publishing itself is behind a hard gate.

Installers are attached below.

### New

- **First-run flow rebuilt** — the ten setup steps are now glass cards; accounts, keys and subscription quota each open their own configuration flow, and tapping a card expands it into a scrollable list. The last step writes two files: the profile and the disposition.
- **Act on a selection** — select a passage in a conversation or in a file in the side panel and a row of buttons appears in place: copy, quote, send to another agent, or have an agent rewrite that passage. Sending jumps to the other agent with the passage already in its composer.
- **Quotes and attachments become a chip in the text** — no longer a grey bar that cannot be removed; hover shows a preview.
- **Read long text in full** — the hover card on long passages is no longer truncated; it scrolls and can be selected.
- **Edit memory entry by entry** — memory can now be changed for specific entries only, leaving the other bytes untouched.

### Improved

- **Dossier card** — everything in the upper-right corner of a conversation card shares one container; no borders, collapsible sections, a draggable overlay, and automatic collapse to icons on narrow windows.
- **The memory screen** — one row per document, entries move into a drawer, the four section names follow the interface language, and the status column became a summary of each principal.
- **Software names for external tools** — configuration now stores the software name; the gateway's machine identifiers no longer reach the screen, and older identifiers are still recognised when read.
- **Exocortex naming** — the external brain is called Exocortex in user-facing text; code identifiers keep their original names.
- **Role names settled** — the conductor and the principals no longer use their old codes; file names and internal identifiers follow.
- **Each collection leg owns its process** — a leg starts and collects within its own round and leaves nothing resident; a leg already running is left alone.
- **Identity for WeChat and DingTalk** — who said it, real names, whether it was sent by the account owner, group chat and sender labels, non-text messages and real file bytes are now carried through.
- **One write point, one trigger point** — collection has a single entry for storage and a single entry for triggering, so a new leg no longer has to guess which existing one to copy.
- **A hard gate on publishing** — one source of truth for the version number, both language versions of the notes, and all three platforms' assets; nothing ships if one is missing. A separate reviewer covers what the release page says and who it is written for.

### Fixed

- **Windows installers crashed on script parsing** — scripts containing Chinese now carry the UTF-8 marker, so setup no longer fails on that parse error.
- **A black window when the engine starts** — window suppression lives in one module now, with no flash of a console window.
- **Creating a project twin failed when the brain had no gate** — it is now allowed and creates only the dossier and memory files, touching nothing inside the brain.
- **Backups missed the official memory** — now that the external memory service no longer manages the official brain, backups ask the brain for its root themselves.
- **Memory reminders could be switched off** — they can no longer be turned off by accident, and the hard-coded contract sits at the top of the identity block.
- **Model routing** — only two roles are recognised; retired tiers are neither read nor written back.
- **Log noise** — the same sentence is not written twice, and the machine no longer talks to itself.

### Install

Download the installer below and run it — `.exe` (data directory can be chosen) or `.msi` (default location) on Windows, `.dmg` on macOS, `.deb` / `.rpm` / `.AppImage` on Linux.

### Documentation

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.2...v0.3.3)
