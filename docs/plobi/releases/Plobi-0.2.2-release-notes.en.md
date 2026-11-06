# Plobi v0.2.2

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.2.2) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.2.2/Plobi-0.2.2-release-notes.en.md)

v0.2.2 addresses several problems around installation and daily use: the local quota gateway failing to start, entries that fail when opened, agenda entries scheduled twice, and two collection columns reading the wrong values.

Installers are attached below.

### Fixed

- **Local quota gateway startup** — The dependency closure now carries `h2`, so the gateway starts; without `h2` it falls back to HTTP/1.1. The gateway's errors are also written to `desktop.log`, instead of a timeout with no reason attached.
- **Usability of roster entries** — An entry that fails when opened is now disabled with the reason attached, instead of letting it be opened and fail.
- **Timetable de-duplication** — Planning compares the entries already scheduled for that day: the proposal is rejected, and the entry is removed again before it is stored. The test is the same slot with the same title.
- **Read-only reconciliation command** — A new reconciliation command. While the rate limit interval has not elapsed it does not contact the school at all and only reports how long is left; it can also show the library side alone next to the requests of the previous round.
- **Conversation and visibility columns in the information pool** — The pool itself is now in the repository and stores both columns, and reads can filter on them. The collection side defines where each value comes from, namely the window username and whether the message is a group message; a verification command pins the shape and the meaning of the time field. The WeChat collector does not write to the pool in this release yet.

### New

- **Memory read and write** — Two actions, `memory_read(scope)` and `memory_write(scope, parts, source)`, available to every role by default.
- **L0 memory layer** — On by default. When a slot reaches its squeeze threshold, the entries it displaces enter the confirmation layer instead of being dropped silently.
- **Scheduled jobs name a recipient** — Creating and updating a job both accept the name of an agent from the roster; without one the job belongs to the currently speaking agent, and when that cannot be determined it is reported in the result.

### Install

Download the installer below and run it — Windows uses `.exe` or `.msi`, macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.2.2...main)
