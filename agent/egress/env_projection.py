"""Project the egress table onto the keys a spawned subprocess can honor.

git, npm, pip, uv and the external agent CLIs all read ``HTTPS_PROXY`` /
``NO_PROXY`` style variables, so the table's only available vocabulary there is
"one proxy plus a bypass list".  That vocabulary is **not symmetric**:

* catch-all = a named exit, some hosts direct ⇒ lossless (exit + bypass list).
* catch-all = direct, some hosts on a named exit ⇒ **inexpressible**; the exit is
  dropped for children.  ``Table.env_dropped_rules`` names those rules and
  :func:`agent.egress.rules.load_table` warns about them at load, so this is a
  reported ceiling rather than a silent degradation (in-process routing is
  unaffected).

Other asymmetries that stay visible:

* ``geosite:`` rules cannot be projected — only the engine resolves them.
* CIDR entries are written through (curl honors them;
  ``urllib.request.proxy_bypass_environment`` does not).
* The five network keys are **owned** by this function when a table exists: a
  stale ``NO_PROXY`` inherited from the parent must not silently alter routing.
"""

from __future__ import annotations

import logging

from typing import MutableMapping

from agent.egress.rules import Table, _expands_to_env_entries

_log = logging.getLogger(__name__)

PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
BYPASS_KEYS = ("NO_PROXY",)
# Both cases, both when clearing and when writing: on POSIX they are different
# variables and curl/libcurl prefer the lowercase one, so clearing only the
# uppercase names would leave an inherited `http_proxy` routing traffic the table
# never approved.  (Windows lookups are case-insensitive, so this costs nothing there.)
ALL_KEYS = tuple(
    list(PROXY_KEYS) + list(BYPASS_KEYS) + [k.lower() for k in PROXY_KEYS + BYPASS_KEYS]
)

#: Mirrors are per-tool variables, not routes — see rules.py for why pypi is refused.
MIRROR_ENV_KEYS = {
    "npm": "npm_config_registry",
    "electron": "ELECTRON_MIRROR",
}


def apply_egress_env(
    env: MutableMapping[str, str],
    table: Table,
    *,
    gate=None,
) -> MutableMapping[str, str]:
    """Mutate ``env`` in place to match ``table``; return it for chaining.

    ``gate`` is the seam that decides whether the catch-all exit exists right now;
    it defaults to :func:`agent.egress.engine.usable_exit`, which is the *same*
    gate our own HTTP clients go through.  Tests pass their own.
    """
    if gate is None:
        from agent.egress.engine import usable_exit

        gate = usable_exit

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

    proxy_url, _reason = gate(table.proxy_url(catch_all.via[len("proxy:") :]))
    if not proxy_url:
        return env

    for key in PROXY_KEYS:
        env[key] = proxy_url
        env[key.lower()] = proxy_url

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
        # A range reaches a child only if NO_PROXY can say it at all: loopback is
        # expanded to the names people use to reach ourselves, and every other range
        # is skipped here and named by `Table.env_dropped_rules` instead of being
        # emitted in a form urllib/httpx silently ignore.
        entries.extend(_expands_to_env_entries(rule))
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        if entry.lower() in seen:
            continue
        seen.add(entry.lower())
        out.append(entry.lower())
    return sorted(out)
