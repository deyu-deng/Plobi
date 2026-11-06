# Plobi v0.2.1

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.2.1) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.2.1/Plobi-0.2.1-release-notes.en.md)

This is a hotfix. v0.2.0 crashed on startup after install; this one fixes that.

The installer is below.

### Fixed

- **Startup no longer crashes** — The optimistic placeholder message was missing a piece of metadata, and the chat runtime threw when it read it. The release build crashed on first launch; this release normalises it.

### Improved

- **A new machine no longer arrives pre-seeded** — Creating one leaves only sessions and logs; the `.env` and `SOUL.md` legs are gone.
- **Quota balance readout completed** — When it can read a balance it now reports the number with its unit, and when it cannot it returns `None` with a reason instead of something half-done.

### Install

Download the installer below — Windows uses `.exe` or `.msi`, macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.2.1...v0.2.2)
