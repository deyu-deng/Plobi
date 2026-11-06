# Plobi v0.2.0

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.2.0) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.2.0/Plobi-0.2.0-release-notes.en.md)

This release fills in three things that were missing: sources you can install yourself, a model per agent, and an agenda that the secretary plans.

Installers are below for Windows, macOS and Linux.

### New

- **Bring your own sources** — Settings has a sources area. Each source carries its own icon and setup form, so it works as soon as you install it; third-party source packs work too. The page shows what you actually installed and leaves out categories you do not have.
- **A model per agent** — Every row in the agent roster picks its own model, or follows the default. Tool configuration is shared, but each agent can switch its own tools on or off.
- **The rail is just agents now** — The profile page, its command palette entries and its shortcuts are gone. All five kinds of row in the left rail share one look, and the agenda keeps a single entry.
- **The secretary plans your day** — The 20:00 run lays out tomorrow first, then reads it to you. Change the plan by talking ("move that afternoon class to three"), or tick off the lines you finished in the UI.
- **Quota readout** — The desktop can tell whether a given source is usable right now and how much you spent today. When remaining balance cannot be read, it stays empty rather than showing an estimate.
- **Network egress panel** — Where outbound traffic goes is configurable in the UI, and saving applies it.
- **Scheduled jobs name a recipient** — A job record carries who runs it, and it does not start without one.
- **Notes moved into the right rail** — Notes now live in the existing preview panel, and selected text can be added to the conversation.
- **DingTalk registered** — DingTalk is registered as a message source. Sending and receiving run through the gateway; the collector does not backfill history yet.

### Improved

- **One local backend** — Several backend processes collapsed into one, with identity travelling on the request. Usage and overview read from the same source as the roster.
- **Sessions are attributed to agents** — Every session records its owner, and cross-agent search no longer mixes databases.
- **The quota card speaks plainly** — It stops saying "not connected" after it connects on startup. Disconnecting is a button, not a verdict.
- **The status bar stops printing codes** — "deferred" reads as "this round is not wired up yet", "red" as "tried, did not come up".
- **The WeChat collector heals itself** — The collection subprocess restarts itself after an unexpected exit. The real cause of the 503 — the key was passed on the command line — is fixed.

### Fixed

- **"Plan tomorrow" no longer wipes what is already scheduled** — A missing action is refused outright, and the call is now read-only.
- **"Tomorrow" in the morning report no longer quietly means today.**
- **Roster entries without a home are no longer treated as connectable agents** — They no longer restart forever, and they no longer take the whole roster down with a 500.
- **The Network panel opens** — Its navigation id was missing from the route allowlist.

### Install

Download the installer below — Windows uses `.exe` or `.msi`, macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

<details>
<summary>Run from source</summary>

```bash
git clone https://github.com/deyu-deng/Plobi.git
cd Plobi
uv venv --python 3.13 && uv sync --extra dev --extra acp --extra messaging --extra web --extra anthropic
cd apps/desktop && npm install && npm run dev
```

</details>

### Docs

- [README](https://github.com/deyu-deng/Plobi#readme)
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.2.0...v0.2.1)
