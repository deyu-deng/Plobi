"""The egress routing table — one truth, several projections (WP-EGRESS-ROUTING).

``network.egress`` in ``config.yaml`` is the only place per-destination routing
is declared.  Every consumer reads it through :func:`load_table`:

* ``agent.process_bootstrap._get_proxy_for_base_url`` — in-process HTTP clients
* :mod:`agent.egress.env_projection` — spawned subprocesses (git, npm, uv, ...)
* :mod:`agent.egress.mihomo_config` — the optional external rule engine

An empty ``rules`` list means "no opinion": callers keep the previous env-only
behaviour.  That is deliberate so this module can be merged before any rule
exists on disk.

Scope limits that must not be papered over:

* ``geosite:`` matchers are **engine-only**.  Resolving them in-process would
  mean shipping GeoSite data files, so they are recorded in
  :attr:`Table.engine_only_rules` and skipped by :func:`decide`.
* A mirror is a URL *rewrite*, not a route.  Only hosts whose tool honors an
  index/registry variable can be mirrored (``npm``, ``electron``); HTTPS
  forward proxying cannot rewrite Host/SNI without a MITM CA.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from utils import base_url_hostname, normalize_proxy_url

DIRECT = "direct"

#: ``mirrors`` keys we will honour, and why the rest are refused.
ALLOWED_MIRROR_KEYS = ("npm", "electron")

_PYPI_MIRROR_REFUSAL = (
    "mirrors.pypi is refused: injecting PIP_INDEX_URL would bypass the "
    "supply-chain policy in tools/lazy_deps.py (no --index-url overrides), which "
    "exists because a config-supplied index can hijack what gets installed. "
    "Pending ruling (Docs/DECISIONS.md 待决 #30)."
)

_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
_GEOSITE_RE = re.compile(r"^geosite:[A-Za-z0-9_&.-]+$")

_TABLE_CACHE: Optional["Table"] = None
_UNREADABLE_REPORTED = False


class EgressConfigError(ValueError):
    """The declared table is malformed or claims something this layer cannot do."""


@dataclass(frozen=True)
class Rule:
    match: str
    via: str
    kind: str  # exact | suffix | cidr | all | geosite
    value: str


@dataclass(frozen=True)
class Table:
    rules: tuple[Rule, ...] = ()
    proxies: dict[str, str] = field(default_factory=dict)
    mirrors: dict[str, str] = field(default_factory=dict)
    engine_enabled: bool = False
    engine_binary: str = ""
    engine_mixed_port: int = 0

    @property
    def active(self) -> bool:
        return bool(self.rules)

    @property
    def engine_only_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind == "geosite")

    def proxy_url(self, name: str) -> str:
        try:
            return self.proxies[name]
        except KeyError:
            raise EgressConfigError(
                f"network.egress.rules references proxy:{name} but "
                f"network.egress.proxies has no such entry (declared: "
                f"{sorted(self.proxies) or 'none'})"
            ) from None


@dataclass(frozen=True)
class Decision:
    proxy_url: Optional[str]
    rule: Rule


def _compile_rule(entry: Any, index: int) -> Rule:
    where = f"network.egress.rules[{index}]"
    if not isinstance(entry, dict):
        raise EgressConfigError(f"{where} must be a mapping, got {type(entry).__name__}")
    match = str(entry.get("match", "")).strip().lower()
    via = str(entry.get("via", "")).strip().lower()
    if not match:
        raise EgressConfigError(f"{where}.match is required")
    if not via:
        raise EgressConfigError(f"{where}.via is required ('direct' or 'proxy:<name>')")
    if via == DIRECT:
        kind = _matcher_kind(match, where)
        return Rule(match=match, via=via, kind=kind, value=_matcher_value(match, kind))
    if via.startswith("proxy:"):
        name = via[len("proxy:") :].strip()
        if not name:
            raise EgressConfigError(f"{where}.via '{via}' names no proxy")
        kind = _matcher_kind(match, where)
        return Rule(match=match, via=via, kind=kind, value=_matcher_value(match, kind))
    raise EgressConfigError(
        f"{where}.via must be 'direct' or 'proxy:<name>', got '{via}'"
    )


def _matcher_kind(match: str, where: str) -> str:
    if _GEOSITE_RE.match(match):
        return "geosite"
    if match == "*":
        return "all"
    if match.startswith("*.") or match.startswith("."):
        bare = match.lstrip("*.")
        if not bare or "." not in bare:
            raise EgressConfigError(f"{where}.match '{match}' needs a domain, not a TLD")
        return "suffix"
    if "/" in match:
        try:
            ipaddress.ip_network(match, strict=True)
        except ValueError as exc:
            raise EgressConfigError(f"{where}.match '{match}' is not a CIDR: {exc}") from exc
        return "cidr"
    if "*" in match:
        raise EgressConfigError(
            f"{where}.match '{match}' — only '*.example.com' (subdomain) and '*' "
            f"(catch-all) are supported; interior wildcards are not"
        )
    if "." not in match and ":" not in match:
        raise EgressConfigError(f"{where}.match '{match}' is not a host, suffix, CIDR or '*'")
    return "exact"


def _matcher_value(match: str, kind: str) -> str:
    if kind == "suffix":
        return match.lstrip("*.")
    return match


def _proxy_url_from_entry(name: str, entry: Any) -> str:
    where = f"network.egress.proxies.{name}"
    if not isinstance(entry, dict):
        raise EgressConfigError(f"{where} must be a mapping with 'url' or 'url_env'")
    url_env = str(entry.get("url_env", "")).strip()
    inline = str(entry.get("url", "")).strip()
    if url_env and inline:
        raise EgressConfigError(f"{where} declares both 'url' and 'url_env'; pick one")
    if url_env:
        inline = os.environ.get(url_env, "").strip()
        if not inline:
            raise EgressConfigError(
                f"{where}.url_env names {url_env} but that variable is not set "
                f"(a proxy URL carrying credentials belongs in the root .env)"
            )
    if not inline:
        raise EgressConfigError(f"{where} has no proxy URL to use")
    normalized = normalize_proxy_url(inline)
    scheme = urlparse(normalized or inline).scheme.lower()
    if scheme not in _PROXY_SCHEMES:
        raise EgressConfigError(
            f"{where} scheme '{scheme or '(none)'}' is unsupported; expected one of "
            f"{', '.join(_PROXY_SCHEMES)}"
        )
    return normalized


def parse_table(raw: Any) -> Table:
    """Validate a ``network.egress`` mapping into a :class:`Table`."""
    if raw is None or raw == {}:
        return Table()
    if not isinstance(raw, dict):
        raise EgressConfigError(f"network.egress must be a mapping, got {type(raw).__name__}")

    proxies_raw = raw.get("proxies") or {}
    if not isinstance(proxies_raw, dict):
        raise EgressConfigError("network.egress.proxies must be a mapping of name -> {url|url_env}")
    proxies = {
        str(name): _proxy_url_from_entry(str(name), entry)
        for name, entry in proxies_raw.items()
    }

    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, Iterable) or isinstance(rules_raw, (str, bytes)):
        raise EgressConfigError("network.egress.rules must be a list")
    rules = tuple(_compile_rule(entry, i) for i, entry in enumerate(rules_raw))
    if rules and rules[-1].kind != "all":
        raise EgressConfigError(
            "network.egress.rules must end with a catch-all {match: '*'} rule — "
            "without it there is no answer for 'everything not listed', and a "
            "subprocess environment cannot express an allow-list-only route"
        )
    for rule in rules:
        if rule.via != DIRECT and not rule.via.startswith("proxy:"):  # pragma: no cover
            raise EgressConfigError(f"rule '{rule.match}' has an unusable via '{rule.via}'")
        if rule.via.startswith("proxy:"):
            table_probe = Table(proxies=proxies)
            table_probe.proxy_url(rule.via[len("proxy:") :])

    mirrors_raw = raw.get("mirrors") or {}
    if not isinstance(mirrors_raw, dict):
        raise EgressConfigError("network.egress.mirrors must be a mapping")
    for key in mirrors_raw:
        if key == "pypi":
            raise EgressConfigError(_PYPI_MIRROR_REFUSAL)
        if key not in ALLOWED_MIRROR_KEYS:
            raise EgressConfigError(
                f"network.egress.mirrors.{key} is not a supported mirror target; "
                f"supported: {', '.join(ALLOWED_MIRROR_KEYS)}"
            )
    mirrors = {str(k): str(v).strip() for k, v in mirrors_raw.items() if str(v).strip()}

    engine_raw = raw.get("engine") or {}
    if engine_raw and not isinstance(engine_raw, dict):
        raise EgressConfigError("network.egress.engine must be a mapping")
    mixed_port = engine_raw.get("mixed_port", 0) or 0
    try:
        mixed_port = int(mixed_port)
    except (TypeError, ValueError):
        raise EgressConfigError(
            f"network.egress.engine.mixed_port must be a port number, got {mixed_port!r}"
        ) from None

    return Table(
        rules=rules,
        proxies=proxies,
        mirrors=mirrors,
        engine_enabled=bool(engine_raw.get("enabled", False)),
        engine_binary=str(engine_raw.get("binary", "") or "").strip(),
        engine_mixed_port=mixed_port,
    )


def load_table(*, config: dict | None = None, force_reload: bool = False) -> Table:
    """Read ``network.egress`` from the merged config, caching per process.

    Pass ``config`` to resolve against a specific mapping (tests, one-shot
    tooling) without touching the cache.

    Two different failures are deliberately treated differently:

    * a **malformed table** raises :class:`EgressConfigError` wherever it is
      read.  It is the user's declaration, and guessing a route for a host is
      worse than stopping.
    * a **config that cannot be read at all** (no resolvable home, unreadable
      file) is reported once and treated as "no table", because this runs inside
      the per-spawn environment builder — a spawn path that cannot start a
      process because config plumbing failed is a new outage, not a rule.
      It caches that verdict so the attempt is not repeated per command.
    """
    global _TABLE_CACHE, _UNREADABLE_REPORTED
    if config is not None:
        return _report_engine_only(
            parse_table((config.get("network") or {}).get("egress"))
        )
    if _TABLE_CACHE is not None and not force_reload:
        return _TABLE_CACHE
    try:
        from plobi_cli.config import load_config_readonly

        raw = (load_config_readonly().get("network") or {}).get("egress")
    except EgressConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - unreadable config is not an unfixable one
        if not _UNREADABLE_REPORTED:
            _UNREADABLE_REPORTED = True
            logging.getLogger(__name__).warning(
                "network.egress: config could not be read (%s) — egress routing stays "
                "inert for this process and the inherited environment decides; "
                "restart after fixing it",
                exc,
            )
        _TABLE_CACHE = Table()
        return _TABLE_CACHE
    _TABLE_CACHE = _report_engine_only(parse_table(raw))
    return _TABLE_CACHE


def _report_engine_only(table: Table) -> Table:
    """Name the rules this layer cannot honor, instead of quietly ignoring them.

    A ``geosite:`` matcher needs the engine's GeoSite data; with the engine off
    the user believes a rule is routing traffic that is actually falling through
    to the catch-all.  The one place that asymmetry can still be surfaced before
    the request goes out is here.
    """
    if table.engine_only_rules and not table.engine_enabled:
        logging.getLogger(__name__).warning(
            "network.egress: %d rule(s) %s are engine-only and do nothing until "
            "network.egress.engine.enabled is true — traffic matching them falls "
            "through to the catch-all rule",
            len(table.engine_only_rules),
            [r.match for r in table.engine_only_rules],
        )
    return table


def default_decision(table: Table) -> Optional[Decision]:
    """The table's answer when the caller cannot name a destination.

    ``agent.external_offload`` and the keepalive client builder both ask this
    question ("what exit do I hand a child that may reach anything").  Without
    it a declared table would silently not apply to those callers, which is the
    exact failure 裁定 94 names: a second narrow reader that misses one caller.
    """
    if not table.active:
        return None
    rule = table.rules[-1]
    if rule.via == DIRECT:
        return Decision(proxy_url=None, rule=rule)
    return Decision(proxy_url=table.proxy_url(rule.via[len("proxy:") :]), rule=rule)


def decide(table: Table, host: str) -> Optional[Decision]:
    """First-match winner for ``host``, or None when the table has no opinion."""
    if not table.active:
        return None
    host = (host or "").strip().lower().rstrip(".")
    for rule in table.rules:
        if not _rule_matches(rule, host):
            continue
        if rule.kind == "geosite":
            continue
        if rule.via == DIRECT:
            return Decision(proxy_url=None, rule=rule)
        return Decision(proxy_url=table.proxy_url(rule.via[len("proxy:") :]), rule=rule)
    return None


def decide_for_url(table: Table, base_url: str) -> Optional[Decision]:
    host = base_url_hostname(base_url)
    if not host:
        return None
    return decide(table, host)


def _rule_matches(rule: Rule, host: str) -> bool:
    if not host:
        return False
    if rule.kind == "all":
        return True
    if rule.kind == "exact":
        return host == rule.value
    if rule.kind == "suffix":
        return host == rule.value or host.endswith("." + rule.value)
    if rule.kind == "cidr":
        try:
            return ipaddress.ip_address(host) in ipaddress.ip_network(rule.value)
        except ValueError:
            return False
    return False
