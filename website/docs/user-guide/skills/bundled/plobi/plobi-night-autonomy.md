---
title: "Plobi Night Autonomy — Night mode tick — run low-risk queued work, hold high-risk, prepare morning report"
sidebar_label: "Plobi Night Autonomy"
description: "Night mode tick — run low-risk queued work, hold high-risk, prepare morning report"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Plobi Night Autonomy

Night mode tick — run low-risk queued work, hold high-risk, prepare morning report.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/plobi/night-autonomy` |
| Version | `0.2.0` |
| Author | Plobi |
| License | MIT |
| Tags | `plobi`, `night`, `cron` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Plobi loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Plobi Night Autonomy

Schedule with Plobi **cron** (do not build a new scheduler).

1. `plobi` `area=ops` `action=night_tick`.
2. If claimed: `area=compute` `action=route` then workers; `area=task` `action=complete` with summary only.
3. Morning: skill `plobi-morning-report`.
