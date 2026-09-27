---
title: "Plobi Passive Learn — Observe repeated human/agent operations and draft Skills for human confirmation"
sidebar_label: "Plobi Passive Learn"
description: "Observe repeated human/agent operations and draft Skills for human confirmation"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Plobi Passive Learn

Observe repeated human/agent operations and draft Skills for human confirmation. Do not auto-install Skills.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/plobi/passive-learn` |
| Version | `0.2.0` |
| Author | Plobi |
| License | MIT |
| Tags | `plobi`, `learning`, `skills` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Plobi loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Plobi Passive Learn

## Steps

1. `plobi` `area=ops` `action=learn_observe` with title + steps.
2. `action=learn_drafts` after repeats.
3. Human approves → `action=learn_resolve` approve=true.
4. Materialize with Plobi `skill_manage` only after approval.
