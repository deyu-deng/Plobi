# Plobi v0.1.0

[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.1.0) | [English](https://github.com/deyu-deng/Plobi/releases/download/v0.1.0/Plobi-0.1.0-release-notes.en.md)

v0.1.0 is the first public release of Plobi. It is built on the Hermes Agent foundation and ships an installable desktop application together with the backend behind it.

Installers are attached below, for 64-bit Windows only.

### New

- **Desktop console** — An Electron application. The left rail lists the agents, the centre holds the conversation, and the right rail carries the agenda timeline, a day view shown in the chief secretary's shell.
- **Local AI gateway** — An OpenAI-compatible `/v1/chat/completions` endpoint on the local machine, adapting Cursor, Antigravity, WorkBuddy and Marvis behind one entry point. A conversation stays bound to the same account through `x-session-id`, and the gateway moves to the next source when one errors.
- **Quota pool** — The backend probes every source and records one of three health states: usable, degraded or unavailable. Dispatch prefers a healthy source, marks a failing one and moves on. A readable balance is reported; an unreadable one leaves the field empty.
- **Agenda** — Schedule entries are extracted from chat logs and wait in a pending confirmation before they are written to the calendar. The agenda is presented as a list board and a day timeline.
- **Secretary and profiles** — The chief secretary handles the conversation and delegates; L2 profiles take on projects. The planner converts each profile's own weekly rhythm into a daily time budget.
- **Sessions and memory** — Sessions are searchable with FTS5 full-text search, including Chinese substring matching. `/learn` and the background review turn proven approaches into reusable skills.
- **Butler** — Five scheduled jobs: the morning report at 07:30, the message digest at 12:00, the quota warning at 08:00, the evening plan at 20:00 and the daily check-in at 21:30.

### Install

Download the Windows installer below and run it. There are no prerequisites beyond a 64-bit Windows system.

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
- [Full changelog](https://github.com/deyu-deng/Plobi/compare/v0.1.0...v0.2.0)
