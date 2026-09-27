---
title: "Plobi Self Upgrade — Run self-diagnosis, propose module upgrades, enqueue L4 changes for human acceptance after tests"
sidebar_label: "Plobi Self Upgrade"
description: "Run self-diagnosis, propose module upgrades, enqueue L4 changes for human acceptance after tests"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Plobi Self Upgrade

Run self-diagnosis, propose module upgrades, enqueue L4 changes for human acceptance after tests.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/plobi/self-upgrade` |
| Version | `0.2.0` |
| Author | Plobi |
| License | MIT |
| Tags | `plobi`, `diagnose`, `upgrade` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Plobi loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Plobi Self Upgrade

## Steps

1. `plobi` `area=ops` `action=diagnose`.
2. Present findings; L4 already blocked for human.
3. After approval, implement + test; never silent-merge L4.
