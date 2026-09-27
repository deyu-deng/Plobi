---
title: "Plobi Quota Alert — Warn when AI free-tier or paid subscription quotas are low"
sidebar_label: "Plobi Quota Alert"
description: "Warn when AI free-tier or paid subscription quotas are low"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Plobi Quota Alert

Warn when AI free-tier or paid subscription quotas are low. Use for 额度预警 / subscription burn alerts.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/plobi/quota-alert` |
| Version | `0.2.0` |
| Author | Plobi |
| License | MIT |
| Tags | `plobi`, `butler`, `quota`, `aigw` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Plobi loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Plobi Quota Alert

## Steps

1. Read aigw / desktop quota signals (reuse aigw — do not scrape if API exists).
2. If low: `plobi` `area=task` `action=enqueue` goal="Quota alert: …" risk=L0.
3. `plobi` `area=preview` `action=push` priority=resource.
4. `plobi` `area=compute` `action=route` to steer work off exhausted surfaces.
