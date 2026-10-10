"""Project the egress table onto the keys a spawned subprocess can honor.

git, npm, pip, uv and the external agent CLIs all read ``HTTPS_PROXY`` /
``NO_PROXY`` style variables, so the table's only available vocabulary there is
"one proxy plus a bypass list".  That is expressive enough because
:func:`agent.egress.rules.parse_table` requires a catch-all rule, but the
asymmetry must stay visible:

* ``geosite:`` rules cannot be projected — only the engine resolves them.
* CIDR entries are written through (curl honors them;
  ``urllib.request.proxy_bypass_environment`` does not).
* The five network keys are **owned** by this function when a table exists: a
  stale ``NO_PROXY`` inherited from the parent must not silently alter routing.
"""

from __future__ import annotations

from typing import MutableMapping

from agent.egress.rules import Table

PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
BYPASS_KEYS = ("NO_PROXY",)
ALL_KEYS = tuple(list(PROXY_KEYS) + list(BYPASS_KEYS) + [k.lower() for k in BYPASS_KEYS])

#: Mirrors are per-tool variables, not routes — see rules.py for why pypi is refused.
MIRROR_ENV_KEYS = {
    "npm": "npm_config_registry",
    "electron": "ELECTRON_MIRROR",
}


def apply_egress_env(env: MutableMapping[str, str], table: Table) -> MutableMapping[str, str]:
    """Mutate ``env`` in place to match ``table``; return it for chaining."""
    for key, url in table.mirrors.items():
        env[MIRROR_ENV_KEYS[key]] = url

    if not table.active:
        return env

    for key in ALL_KEYS:
        env.pop(key, None)

    catch_all = table.rules[-1]
    if catch_all.via == "direct":
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        return env

    proxy_url = table.proxy_url(catch_all.via[len("proxy:") :])
    for key in PROXY_KEYS:
        env[key] = proxy_url

    bypass = _bypass_entries(table)
    if bypass:
        env["NO_PROXY"] = ",".join(bypass)
        env["no_proxy"] = env["NO_PROXY"]
    return env


def _bypass_entries(table: Table) -> list[str]:
    entries: list[str] = []
    for rule in table.rules:
        if rule.via != "direct" or rule.kind == "geosite":
            continue
        if rule.kind == "all":
            continue
        entries.append(rule.value)
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        if entry.lower() in seen:
            continue
        seen.add(entry.lower())
        out.append(entry.lower())
    return sorted(out)
