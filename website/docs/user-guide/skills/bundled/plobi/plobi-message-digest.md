---
title: "Plobi Message Digest"
sidebar_label: "Plobi Message Digest"
description: "Extract hard deadlines and todos from WeChat/DingTalk/email digests into the North Star task queue"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Plobi Message Digest

Extract hard deadlines and todos from WeChat/DingTalk/email digests into the North Star task queue. Use for butler message扫描 / DDL extraction.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/plobi/message-digest` |
| Version | `0.2.0` |
| Author | Plobi |
| License | MIT |
| Tags | `plobi`, `butler`, `digest` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Plobi loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Plobi Message Digest

## Steps

1. Scan message sources (existing wechat-cli / dingtalk / Mind skills — reuse, don't rewrite).
2. For each hard DDL: `plobi` `area=task` `action=enqueue` with risk `L0` or `L2` if a send is required.
3. `plobi` `area=preview` `action=push` priority=progress.
4. `plobi` `area=ops` `action=master_summarize` — never dump raw chats into Master.
