# plobi-north-star

Deep edge module for the Plobi North Star. **Reuse Hermes**; expose a narrow API.

## Public interfaces only

| Surface | What |
|---|---|
| Agent tool | `plobi` (`area` + `action`) |
| HTTP | `/api/plugins/plobi-north-star/*` |
| Docs | [`docs/plobi/north_star/API.md`](../../docs/plobi/north_star/API.md) |

Do **not** import `lib/queue.py`, `lib/hid/*`, etc. from Electron.

## Enable

```yaml
plugins:
  enabled:
    - plobi-north-star
```

Include toolset `plobi_north_star` (see `docs/plobi/profiles/master/config.yaml`).

## Reuse

- Board collaboration → Hermes **kanban** (mirrored on enqueue)
- Messaging / mobile → Hermes **gateway** + `/plobi …`
- Schedules → Hermes **cron** + `skills/plobi/*`
- Antigravity → **aigw**
- Marvis GUI → HID (owned here)

## HID

Default mock-safe. Real Pico: `PLOBI_PICO_SERIAL` + `plobi` `area=compute action=hid_run mock=false`.
