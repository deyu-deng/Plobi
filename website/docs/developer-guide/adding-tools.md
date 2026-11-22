---
sidebar_position: 2
title: "Adding Tools"
description: "How to add a new tool to Plobi Agent — schemas, handlers, registration, and toolsets"
---

# Adding Tools

Before writing a tool, ask yourself: **should this be a [skill](creating-skills.md) instead?**

:::warning Built-in Core Tools Only
This page is for adding a **built-in Plobi tool** to the repository itself.
If you want a personal, project-local, or otherwise custom tool without
modifying Plobi core, use the plugin route instead:

- [Plugins](/user-guide/features/plugins)
- [Build a Plobi Plugin](/developer-guide/plugins)

Default to plugins for most custom tool creation. Only follow this page when
you explicitly want to ship a new built-in tool in `tools/` and `toolsets.py`.
:::

Make it a **Skill** when the capability can be expressed as instructions + shell commands + existing tools (arXiv search, git workflows, Docker management, PDF processing).

Make it a **Tool** when it requires end-to-end integration with API keys, custom processing logic, binary data handling, or streaming (browser automation, TTS, vision analysis).

## Overview

Adding a tool touches **2 files**:

1. **`tools/your_tool.py`** — handler, schema, check function, `registry.register()` call
2. **`toolsets.py`** — add tool name to `_PLOBI_CORE_TOOLS` (or a specific toolset)

Any `tools/*.py` file with a top-level `registry.register()` call is auto-discovered at startup — no manual import list required.

## Step 1: Create the Built-in Tool File

Every tool file follows the same structure:

```python
# tools/weather_tool.py
"""Weather Tool -- look up current weather for a location."""

import json
import os
import logging

logger = logging.getLogger(__name__)


# --- Availability check ---

def check_weather_requirements() -> bool:
    """Return True if the tool's dependencies are available."""
    return bool(os.getenv("WEATHER_API_KEY"))


# --- Handler ---

def weather_tool(location: str, units: str = "metric") -> str:
    """Fetch weather for a location. Returns JSON string."""
    api_key = os.getenv("WEATHER_API_KEY")
    if not api_key:
        return json.dumps({"error": "WEATHER_API_KEY not configured"})
    try:
        # ... call weather API ...
        return json.dumps({"location": location, "temp": 22, "units": units})
    except Exception as e:
        return json.dumps({"error": str(e)})


# --- Schema ---

WEATHER_SCHEMA = {
    "name": "weather",
    "description": "Get current weather for a location.",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name or coordinates (e.g. 'London' or '51.5,-0.1')"
            },
            "units": {
                "type": "string",
                "enum": ["metric", "imperial"],
                "description": "Temperature units (default: metric)",
                "default": "metric"
            }
        },
        "required": ["location"]
    }
}


# --- Registration ---

from tools.registry import registry

registry.register(
    name="weather",
    toolset="weather",
    schema=WEATHER_SCHEMA,
    handler=lambda args, **kw: weather_tool(
        location=args.get("location", ""),
        units=args.get("units", "metric")),
    check_fn=check_weather_requirements,
    requires_env=["WEATHER_API_KEY"],
)
```

### Key Rules

:::danger Important
- Handlers **MUST** return a JSON string (via `json.dumps()`), never raw dicts
- Errors **MUST** be returned as `{"error": "message"}`, never raised as exceptions
- The `check_fn` is called when building tool definitions — if it returns `False`, the tool is silently excluded
- The `handler` receives `(args: dict, **kwargs)` where `args` is the LLM's tool call arguments
:::

## Step 2: Add the Built-in Tool to a Toolset

In `toolsets.py`, add the tool name:

```python
# If it should be available on all platforms (CLI + messaging):
_PLOBI_CORE_TOOLS = [
    ...
    "weather",  # <-- add here
]

# Or create a new standalone toolset:
"weather": {
    "description": "Weather lookup tools",
    "tools": ["weather"],
    "includes": []
},
```

## ~~Step 3: Add Discovery Import~~ (No longer needed)

Tool modules with a top-level `registry.register()` call are auto-discovered by `discover_builtin_tools()` in `tools/registry.py`. No manual import list to maintain — just create your file in `tools/` and it's picked up at startup.

## Async Handlers

If your handler needs async code, mark it with `is_async=True`:

```python
async def weather_tool_async(location: str) -> str:
    async with aiohttp.ClientSession() as session:
        ...
    return json.dumps(result)

registry.register(
    name="weather",
    toolset="weather",
    schema=WEATHER_SCHEMA,
    handler=lambda args, **kw: weather_tool_async(args.get("location", "")),
    check_fn=check_weather_requirements,
    is_async=True,  # registry calls _run_async() automatically
)
```

The registry handles async bridging transparently — you never call `asyncio.run()` yourself.

## Handlers That Need task_id

Tools that manage per-session state receive `task_id` via `**kwargs`:

```python
def _handle_weather(args, **kw):
    task_id = kw.get("task_id")
    return weather_tool(args.get("location", ""), task_id=task_id)

registry.register(
    name="weather",
    ...
    handler=_handle_weather,
)
```

## Agent-Loop Intercepted Tools

Some tools (`todo`, `memory`, `session_search`, `delegate_task`) need access to per-session agent state. These are intercepted by `run_agent.py` before reaching the registry. The registry still holds their schemas, but `dispatch()` returns a fallback error if the intercept is bypassed.

## Optional: Setup Wizard Integration

If your tool requires an API key, add it to `plobi_cli/config.py`:

```python
OPTIONAL_ENV_VARS = {
    ...
    "WEATHER_API_KEY": {
        "description": "Weather API key for weather lookup",
        "prompt": "Weather API key",
        "url": "https://weatherapi.com/",
        "tools": ["weather"],
        "password": True,
        "category": "tool",
    },
}
```

`category` decides **where the field shows up** in Settings — it does not enable
anything:

| `category` | Surfaces under | Read by |
| --- | --- | --- |
| `tool` | Tools & Keys → Tools | the tool itself, at call time |
| `provider` | Settings → Providers (model API keys) | the model client |
| `messaging` | Messaging page (or Tools & Keys → Settings for gateway-wide rows) | the gateway adapter |
| `setting` | Tools & Keys → Settings | server / webhook / gateway boot |

Two things worth knowing before you pick `tool`:

- The `tools` field is **documentation only**. It is printed by `plobi env` as a
  hint (`WEATHER_API_KEY → weather`) and returned to the desktop client; nothing
  in tool registration or toolset resolution reads it. A key being present or
  absent never changes which tools are registered — it only changes whether the
  tool works once called.
- `category="tool"` (and `messaging`) puts the variable on the subprocess
  blocklist in `tools/environments/local.py`, so terminals and `execute_code`
  never inherit it. If a shell command is supposed to `curl` the service with
  that credential, use `category="skill"` instead.

Note that a credential is not a toolset switch: toolsets (see `toolsets.py`) are
what gate tools in or out of the model's tool list. See
[Toolsets reference](../reference/toolsets-reference.md) for that axis.

### Optional: give the row a Test button

A row that only stores a key cannot tell the user *whether it works* — a typo sits
there looking healthy until the first real call fails. Register a probe and the
desktop client grows a Test button on that row:

```python
# plobi_cli/env_probe.py
_PROBES = {
    "WEATHER_API_KEY": _probe_weather,
}


def _probe_weather(value: str) -> Dict[str, Any]:
    # One tiny real request against the service — no mocking, no cached answer.
    resp = httpx.get("https://api.weatherapi.com/v1/current.json",
                     params={"key": value, "q": "London"}, timeout=10)
    if resp.status_code in (401, 403):
        return {"ok": False, "reachable": True, "message": str(resp.status_code)}
    return {"ok": True, "reachable": True, "message": "", "sample": resp.json()["location"]["name"]}
```

Each probe returns four things, and the three outcomes stay distinct in the UI:

| Field | Meaning | Shown as |
| --- | --- | --- |
| `ok` | the service accepted the credential | 可用 (green) |
| `reachable=False` | the probe never reached the service (offline, nothing listening) | 连不上服务 (grey) |
| `ok=False, reachable=True` | the service answered and refused the credential | 被拒绝 (red) |
| `sample` | what the service actually returned | appended as proof |

This registry is also the only one for **model provider** keys. The Providers
page (`POST /api/providers/validate`) and Tools & Keys (`POST /api/env/probe`)
both read `_PROBES`; the provider route is now a thin alias that only translates
the answer. Adding a credential is therefore one edit, and a provider key gets a
Test button on both surfaces instead of sending the user to another page to find
out whether it works.

Rules of the registry:

- A key **without** an entry gets no Test button — never a button whose only
  possible answer is "unsupported".
- A probe takes `(value: str, api_key: str = "")`. The second argument is only
  for services that need another secret to be reachable at all (a custom
  `OPENAI_BASE_URL` whose `/models` sits behind auth); ignore it otherwise.
- `reachable=False` must not be reported as a bad credential; a self-hosted
  endpoint that is merely down is not a wrong key.
- The probe runs against the **draft** value in the input when there is one, so a
  mistyped key is caught before saving.
- Prefer a cheap real call over a `/models` style endpoint when the service has
  one, and surface a short `sample` (what was translated, resolved, etc.) — it is
  the difference between "the key was accepted" and "the thing actually works".


## Checklist

- [ ] Tool file created with handler, schema, check function, and registration
- [ ] Added to appropriate toolset in `toolsets.py`
- [ ] Confirmed this really should be a built-in/core tool and not a plugin
- [ ] Handler returns JSON strings, errors returned as `{"error": "..."}`
- [ ] Optional: API key added to `OPTIONAL_ENV_VARS` in `plobi_cli/config.py`
- [ ] Optional: credential probe registered in `plobi_cli/env_probe.py` (gives the row a Test button)
- [ ] Optional: Added to `toolset_distributions.py` for batch processing
- [ ] Tested with `plobi chat -q "Use the weather tool for London"`
