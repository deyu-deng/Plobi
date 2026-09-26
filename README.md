<div align="center">

# Plobi

**AI Secretary for the Multi-App Era**

Plobi is a desktop AI agent that orchestrates your free-tier AI tools — Cursor, Antigravity, WorkBuddy, Marvis, DevEco, and API providers — into one unified secretary. It tracks quotas, dispatches tasks, manages your agenda, and runs unattended automations, so you don't have to jump between apps.

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://www.python.org/)
[![Electron](https://img.shields.io/badge/Electron-40-9feaf4)](https://www.electronjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-blue)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](#license)

</div>

---

## Why Plobi?

Every AI tool gives away free quota, but using them all means constant context-switching. Plobi sits above them all:

- **Unified quota awareness** — knows which tools have remaining quota and routes work accordingly
- **Three-tier delegation** — a secretary (L1) dispatches to project agents (L2), which schedule execution bodies (L3) including external AI apps
- **Local AI gateway** — aggregates desktop AI app quotas behind a single OpenAI-compatible endpoint
- **Agenda & automation** — chatlog-driven schedule extraction, confirmation flow, cron-based digests and alerts
- **Desktop-native** — Electron app with a three-pane console, not a terminal or a web tab

## Features

### Agent Orchestration
- **L1 Secretary** — conversational front door, makes planning decisions, talks to you
- **L2 Project Agents** — domain-specialized, own their context and sub-agent roster
- **L3 Execution Bodies** — built-in tool agents, external AI apps (Cursor, Antigravity, WorkBuddy, Marvis, DevEco), and hardware HID devices

### Quota Management
- **Quota pool** — tracks health and remaining quota across API providers and desktop apps
- **Health probing** — periodic probes with per-source status (healthy / cooldown / reauth / disabled)
- **Failover routing** — automatic source switching when a provider goes unhealthy or hits quota
- **Butler alerts** — scheduled morning reports, todo digests, and quota-warning notifications

### Local AI Gateway (aigw)
- OpenAI-compatible `/v1/chat/completions` endpoint on `localhost`
- Three integration modes: spawn-CLI (compliant), GUI automation, reverse-engineered API
- Sticky sessions, circuit breaking, rate limiting, encrypted credential vault
- Supported: Antigravity, WorkBuddy, Marvis, Cursor (partial)

### Agenda & Memory
- **Chatlog collection** — extracts schedule changes from chat platforms with a privacy-first blacklist
- **Pending confirmation** — proposed events wait for your approval before landing on the calendar
- **Mind memory** — FTS5 session search, skill creation from experience, cross-session recall

### Desktop Console
- Three-pane layout: agent roster · conversation · agenda timeline
- L2 workbench per agent: sub-agent status, outsourced app sessions, files, board, artifacts
- Theme system with seed-color derivation and global density / radius tokens

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, LangGraph agent runtime |
| Desktop | Electron 40, React 19, TypeScript, Vite |
| UI | Tailwind CSS, assistant-ui, custom design-token system |
| Gateway | Python, FastAPI, subprocess + Windows UI Automation |
| Storage | SQLite (agenda, sessions, quota), JSON config |
| Scheduling | Built-in cron with persistent job registry |

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+ and npm
- Git

### Install

```bash
git clone https://github.com/deyu-deng/Plobi.git
cd Plobi/Code

# Python backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -e ".[all]"

# Desktop app
cd apps/desktop
npm install
```

### Run

```bash
# Terminal agent (CLI)
plobi

# Desktop app (dev mode with hot reload)
cd apps/desktop
npm run dev
```

### Configure Providers

Add API keys through the desktop app's Settings → Providers, or via environment variables:

```bash
# OpenAI-compatible providers
export OPENAI_API_KEY=sk-...
# Zhipu / Z.ai
export GLM_API_KEY=...
# Custom OpenAI-compatible endpoint
export OPENAI_BASE_URL=https://api.example.com/v1
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Desktop Console (Electron / React)             │
│  ┌──────────┬──────────────┬────────────────┐  │
│  │ Agents   │ Conversation │ Agenda / Files │  │
│  │ Roster   │ (ChatSurface)│ Timeline       │  │
│  └──────────┴──────────────┴────────────────┘  │
└──────────────────────┬──────────────────────────┘
                       │ HTTP / IPC
┌──────────────────────▼──────────────────────────┐
│  Python Backend                                 │
│  ┌─────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ L1 Sec. │→ │ L2 Agent │→ │ L3 Executors  │  │
│  └─────────┘  └──────────┘  └───────┬───────┘  │
│                                     │           │
│  ┌─────────────┐  ┌────────────┐   │           │
│  │ Quota Pool  │  │  aigw      │←──┘           │
│  │ (health)    │  │ (routing)  │               │
│  └─────────────┘  └─────┬──────┘               │
└──────────────────────────┼──────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       API Providers   Desktop Apps   Hardware HID
       (zhipu, etc.)   (Cursor, ...)   (Pico 2W)
```

## Project Structure

```
Code/
├── agent/              # Core agent runtime (LangGraph)
├── aigw/               # Local AI gateway (OpenAI-compatible)
├── apps/desktop/       # Electron desktop app
├── plobi/             # Plobi-specific modules
│   ├── agenda/         # Agenda service & collectors
│   ├── quota/          # Quota pool & source probes
│   ├── delegation/     # L3 delegation guard
│   ├── butler/         # Scheduled reports & alerts
│   └── collectors/     # Chatlog collection
├── plugins/            # Agent plugins (memory, platforms, etc.)
├── skills/             # Reusable agent skills
├── tools/              # Tool implementations
├── gateway/            # Multi-platform messaging gateway
├── plobi_cli/         # CLI interface
└── tests/              # Test suite
```

## Contributing

Contributions are welcome. Please:

1. Fork the repository and create a feature branch
2. Write tests for new functionality
3. Ensure `pytest` and `npm run test` pass
4. Submit a pull request with a clear description

### Development Notes

- Backend tests: `pytest tests/`
- Frontend type check: `cd apps/desktop && npx tsc --noEmit`
- Frontend tests: `cd apps/desktop && npm run test`

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

Built on the [Hermes Agent](https://github.com/NousResearch/hermes-agent) foundation.

</div>
