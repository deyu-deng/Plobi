---
name: plobi-morning-report
description: Build and deliver the Plobi overnight/morning report (completed, failed, awaiting human). Use after night autonomy or when user asks for 早报.
version: 0.2.0
author: Plobi
license: MIT
metadata:
  plobi:
    tags: [plobi, butler, night, report]
---

# Plobi Morning Report

## Steps

1. Call `plobi` with `area=ops` `action=morning_report`.
2. Optionally `area=task` `action=board` for fresher counts.
3. Deliver markdown; lead with **awaiting approvals**.
4. Do not auto-approve L2+ items. Use Plobi gateway to deliver — do not invent a notifier.
