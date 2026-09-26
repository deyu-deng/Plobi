---
name: plobi-self-upgrade
description: Run self-diagnosis, propose module upgrades, enqueue L4 changes for human acceptance after tests.
version: 0.2.0
author: Plobi
license: MIT
metadata:
  hermes:
    tags: [plobi, diagnose, upgrade]
---

# Plobi Self Upgrade

## Steps

1. `plobi` `area=ops` `action=diagnose`.
2. Present findings; L4 already blocked for human.
3. After approval, implement + test; never silent-merge L4.
