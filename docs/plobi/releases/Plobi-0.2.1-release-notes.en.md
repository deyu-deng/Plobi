# Plobi v0.2.1

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.2.1) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.2.1/Plobi-0.2.1-release-notes.en.md)

v0.2.1 fixes the chat page crash seen after installing v0.2.0, and adjusts the initial file set and the quota balance readout.

Installers are attached below.

### Fixed

- **Chat page crash** — After v0.2.0 was installed, the chat page raised a rendering error whenever an active session existed, because the placeholder message carried no metadata. This release fills in the metadata of that message and pins the message id to the session.

### Improved

- **No seeded files in a new profile** — Creating a profile now creates only `sessions/` and `logs/`, and no longer copies `.env` or `SOUL.md`. The main directory and existing profiles are unaffected.
- **Quota balance readout** — A readable balance returns the figure with its unit (dollars, credits or requests); an unreadable one returns `None` with the specific reason.

### Install

Download the installer below and run it — Windows uses `.exe` or `.msi`, macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.2.1...v0.2.2)
