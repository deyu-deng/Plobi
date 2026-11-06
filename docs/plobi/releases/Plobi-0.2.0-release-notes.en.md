# Plobi v0.2.0

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.2.0) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.2.0/Plobi-0.2.0-release-notes.en.md)

v0.2.0 adds three capabilities that were missing: installable sources, per-agent tool switches, and an agenda the L1 secretary plans.

Installers are attached below for Windows, macOS and Linux.

### New

- **Sources area** — Settings gains a sources area. Every source carries its own icon and setup form, third-party source packs can be installed directly, and categories that are not installed are not shown.
- **Per-agent tool switches** — The right rail can switch off skills, tool sets and MCP servers for each agent, on top of one shared global table. A roster row can already be given its own model on the backend; the model drop-down has not reached the interface yet.
- **The rail carries agents only** — The profile management page, its command palette entries and its shortcuts are gone. All kinds of row in the left rail share one appearance, and the agenda keeps a single entry.
- **L1 planning** — The 20:00 run lays out the next day's plan first and reads it out afterwards, and the job prompt takes effect from the first start of the backend. The plan can be changed in conversation, and finished lines can be ticked off in the interface.
- **Quota read-out** — A read-only quota report states whether each source is usable now and how much was used today. A remaining balance that cannot be read stays empty with its reason attached, and no estimate is substituted; the interface itself currently shows usability only.
- **Network egress panel** — The egress path of outbound traffic is configurable in the interface and applies on save. Saving replaces the whole set, so a rule removed from the panel really disappears.
- **Scheduled jobs name a recipient** — A job record carries the party that runs it, and it does not start when the field is missing or names an agent outside the roster.
- **Notes moved into the right rail** — Notes move from a separate view into the existing preview panel, and selected content can be added to the conversation.
- **DingTalk registered** — DingTalk is registered as a message source, with sending and receiving running through the gateway. The collector is not implemented yet, and the interface marks it as not collecting.

### Improved

- **One local backend** — Several backend processes collapse into one, with identity travelling on the request. Usage and overview read from the same source as the roster; a remote profile still runs its own backend.
- **Session attribution** — Every session records the agent it belongs to, and cross-agent search no longer mixes data.
- **Quota card state** — The card stops reporting "not connected" after it connects on startup, and disconnecting is a button. Disconnecting does not sign out of that application's own account.
- **Status bar text** — The status bar no longer prints internal state codes.
- **WeChat collection recovers on its own** — The collection subprocess restarts itself after an unexpected exit. The root cause of the 503 is fixed: the key used to travel as a command line argument, where a process list on the machine could read it, and is now written to a configuration file.

### Fixed

- **Planning no longer overwrites scheduled blocks** — A missing action is refused outright, and the call is now read-only.
- **Morning report dates** — "Tomorrow" in the morning report is no longer silently resolved as today.
- **Roster entries without a home** — Such entries are no longer treated as connectable profiles, no longer restart forever, and no longer take the whole roster down with a 500. Course data entries stay visible in the roster but are no longer treated as runnable profiles.
- **Network panel entry** — The navigation id of that panel was missing from the route allowlist, so the panel could not be opened.

### Install

Download the installer below and run it — Windows uses `.exe` or `.msi`, macOS uses `.dmg`, Linux uses `.deb`, `.rpm` or `.AppImage`.

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
