---
title: "Plobi Butler Extended — Extended invisible butler — email triage, parcels, calendar conflicts, disk hygiene"
sidebar_label: "Plobi Butler Extended"
description: "Extended invisible butler — email triage, parcels, calendar conflicts, disk hygiene"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Plobi Butler Extended

Extended invisible butler — email triage, parcels, calendar conflicts, disk hygiene. Uses domain slots and risk gates.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/plobi/butler-extended` |
| Version | `0.2.0` |
| Author | Plobi |
| License | MIT |
| Tags | `plobi`, `butler`, `email`, `calendar`, `disk` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Plobi loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Plobi Butler Extended

## Steps

1. `plobi` `area=ops` `action=domain_list` kind=butler.
2. Enqueue with domain default_risk via `area=task` `action=enqueue`.
3. Prefer existing Plobi/Mind integrations for mail/calendar when present.
4. Destructive disk ops need approval (L2+).
