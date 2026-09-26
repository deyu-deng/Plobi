# Langfuse Observability Plugin

This plugin ships bundled with Plobi but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

Pick one:

```bash
# Interactive: walks you through credentials + SDK install + enable
plobi tools  # → Langfuse Observability

# Manual
pip install langfuse
plobi plugins enable observability/langfuse
```

## Required credentials

Set these in `~/.plobi/.env` (or via `plobi tools`):

```bash
PLOBI_LANGFUSE_PUBLIC_KEY=pk-lf-...
PLOBI_LANGFUSE_SECRET_KEY=sk-lf-...
PLOBI_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
plobi plugins list                 # observability/langfuse should show "enabled"
plobi chat -q "hello"              # then check Langfuse for a "Plobi turn" trace
```

## Optional tuning

```bash
PLOBI_LANGFUSE_ENV=production       # environment tag
PLOBI_LANGFUSE_RELEASE=v1.0.0       # release tag
PLOBI_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
PLOBI_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
PLOBI_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
plobi plugins disable observability/langfuse
```
