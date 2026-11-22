## Plobi v0.3.4

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.3.4) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.3.4/Plobi-0.3.4-release-notes.en.md)

v0.3.4 fixes hover tips on the top-bar entries being cut off at the window edge, moves the part that needs administrator rights into a separate sidecar, lets you write egress rules directly, and puts creating and editing an agent into the same container.

Installers are attached below.

### New

- **Write egress rules yourself** — the egress panel gains a Rules page: one rule per line, covering domains, `*.subdomains`, CIDR blocks, category lists, region lists, keywords, regexes, process names and source ranges. An unrecognised prefix is reported right away, with the offending part named. The last line is the default destination: everything unmatched goes there, and it can be neither deleted nor moved up.
- **Test a key where it lives** — credential cards gain a Test button that answers in three separate ways: the key works (with a sample of what came back), the key was rejected, or the service could not be reached.
- **Say what a rename does before it happens** — when renaming an external session, the dialog states up front whether the change syncs into that other app. Renames that stay on the Plobi side are marked Local only.
- **Say where a hand-off stopped** — three states now explain themselves: waiting for a permission grant, waiting for an answer, and the previous message still sending (queued, do not resend).

### Improved

- **Borrow administrator rights for one small job** — taking over traffic needs a network adapter, which needs an administrator process. The old route made the whole app require elevation, so every launch cost a UAC prompt. Now the installer registers a highest-privilege, logon-triggered scheduled task that runs a sidecar doing three things only: listen on a pipe, check a token, and hand the command to the engine and adapter code that already exists. The main process keeps normal rights, so it cannot quietly change other things just because it holds an administrator token. The token is kept in a per-user directory with access restricted to that user; pipe names are derived per data directory, so two profiles do not cross; a second copy exits on its own.
- **One container for creating and editing** — the agent form now shares its shell as well as its fields, so both callers supply data and copy instead of each laying out the dialog. Field order is uniform: name, the middle slot (category when creating, rename list when editing), working folder, appearance. When the submit button is enabled, where errors appear and whether Esc is swallowed now have a single definition. The English-name field says only "lowercase letters, digits, hyphens", with a Retranslate button beside it.
- **One place for probing** — backend environment-variable probing is now a single registry; the old validation endpoint forwards to it instead of judging a second time.
- **Clearer words while updating** — the message shown when the updater takes over is shorter, says plainly that it is still working when no progress has come back, and offers a Details view.

### Fixed

- **Hover tips cut off at the top bar** — the tips on the three entries at the top right of a conversation card (dossier, pending approvals, pending brain writes) are now drawn by the app: native tips are painted by the window itself and are sliced in half when they sit against the top and right edges. The remaining tips of the same kind on the monitor panel's row notes and labels, and on the dossier pin, were switched as well.
- **No more console windows flashing on Windows** — console-window suppression for child processes now lives in one module. Call sites that only set `windowsHide` produced flashes from grandchild processes, and `detached` on Windows overrides the no-window flag, which made the flash certain.

### Install

Download the installer below and run it — `.exe` on Windows (the data directory can be chosen) or `.msi` (default location), `.dmg` on macOS, and `.deb` / `.rpm` / `.AppImage` on Linux.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.3.3...v0.3.4)
