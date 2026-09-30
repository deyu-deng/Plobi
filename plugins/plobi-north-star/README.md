# plobi-north-star

Deep edge module for the Plobi North Star. **Reuse Plobi**; expose a narrow API.

## Public interfaces only

| Surface | What |
|---|---|
| Agent tool | `plobi` (`area` + `action`) |
| Agent tool | `plobi_agent_ask` — ask one project 分身 a question, get its own answer (裁定 50.4) |
| HTTP | `/api/plugins/plobi-north-star/*` |
| Docs | [`docs/plobi/north_star/API.md`](../../docs/plobi/north_star/API.md) |

Do **not** import `lib/queue.py`, `lib/hid/*`, etc. from Electron.

## Asking a 分身 (`plobi_agent_ask`)

`agent_ask.py` resolves the target through `plobi.agents.registry.AgentRegistry`
(archived rows and unknown names are refused, and the refusal lists what IS
askable), then runs that 分身 as a subprocess with the kanban dispatcher's
environment recipe: its own `PLOBI_HOME`, `TERMINAL_CWD` pinned to the project
folder, `--cli --accept-hooks chat -q`. The question carries who is asking; the
分身's own session in its profile `state.db` is the record, and the answer plus
that session id come back to the secretary. No board comment, no second
transcript store, no force-approval flag.

The bounded wait is a `config.yaml` knob (never a `PLOBI_*` env var), clamped to
a hard ceiling in code:

```yaml
plugins:
  entries:
    plobi-north-star:
      agent_ask:
        timeout_seconds: 180   # ceiling 900
```

The tool's `check_fn` hides the schema entirely when the registry has no live
project 分身, so the prompt-cache cost is zero until there is somebody to ask.

## Enable

```yaml
plugins:
  enabled:
    - plobi-north-star
```

Include toolset `plobi_north_star` (see `docs/plobi/profiles/master/config.yaml`).

## Reuse

- Board collaboration → Plobi **kanban** (mirrored on enqueue)
- Messaging / mobile → Plobi **gateway** + `/plobi …`
- Schedules → Plobi **cron** + `skills/plobi/*`
- Antigravity → **aigw**
- Marvis GUI → HID (owned here)

## HID

Default mock-safe. Real Pico: `PLOBI_PICO_SERIAL` + `plobi` `area=compute action=hid_run mock=false`.
