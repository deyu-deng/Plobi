---
name: plobi-night-autonomy
description: Night mode tick — run low-risk queued work, hold high-risk, prepare morning report.
version: 0.2.0
author: Plobi
license: MIT
metadata:
  hermes:
    tags: [plobi, night, cron]
---

# Plobi Night Autonomy

Schedule with Hermes **cron** (do not build a new scheduler).

1. `plobi` `area=ops` `action=night_tick`.
2. If claimed: `area=compute` `action=route` then workers; `area=task` `action=complete` with summary only.
3. Morning: skill `plobi-morning-report`.
