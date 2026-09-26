---
name: plobi-butler-extended
description: Extended invisible butler — email triage, parcels, calendar conflicts, disk hygiene. Uses domain slots and risk gates.
version: 0.2.0
author: Plobi
license: MIT
metadata:
  plobi:
    tags: [plobi, butler, email, calendar, disk]
---

# Plobi Butler Extended

## Steps

1. `plobi` `area=ops` `action=domain_list` kind=butler.
2. Enqueue with domain default_risk via `area=task` `action=enqueue`.
3. Prefer existing Plobi/Mind integrations for mail/calendar when present.
4. Destructive disk ops need approval (L2+).
