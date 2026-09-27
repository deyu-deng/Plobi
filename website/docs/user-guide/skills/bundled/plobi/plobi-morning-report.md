---
title: "Plobi Morning Report — Build and deliver the Plobi overnight/morning report (completed, failed, awaiting human)"
sidebar_label: "Plobi Morning Report"
description: "Build and deliver the Plobi overnight/morning report (completed, failed, awaiting human)"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Plobi Morning Report

Build and deliver the Plobi overnight/morning report (completed, failed, awaiting human). Use after night autonomy or when user asks for 早报.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/plobi/morning-report` |
| Version | `0.2.0` |
| Author | Plobi |
| License | MIT |
| Tags | `plobi`, `butler`, `night`, `report` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Plobi loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Plobi Morning Report

## Steps

1. Call `plobi` with `area=ops` `action=morning_report`.
2. Optionally `area=task` `action=board` for fresher counts.
3. Deliver markdown; lead with **awaiting approvals**.
4. Do not auto-approve L2+ items. Use Plobi gateway to deliver — do not invent a notifier.
