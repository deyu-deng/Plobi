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
    "exists because a config-supplied index can hijack what gets installed.  "
    "Ruled: 待决 #30 answered 2026-10-11 — keep the refusal; a mirror for "
    "user-run pip would need a per-process exemption of that policy, not a "
    "global env injection."
)

_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
_GEOSITE_RE = re.compile(r"^geosite:[A-Za-z0-9_&.-]+$")

#: The only address ranges a spawned child can be told about at all.
#:
#: ``NO_PROXY`` has no range vocabulary, and this is not theory: measured on this
#: box 2026-10-13 against a local listener with a dead proxy, ``curl`` and
#: ``requests`` honour ``127.0.0.0/8``, while ``urllib`` and ``httpx`` ignore the
#: entry outright and hand the request to the proxy — i.e. a child reaching our own
#: gateway on loopback would fail.  Loopback is therefore expanded into the names
#: people actually use to reach ourselves; every other range stays unexpressible
#: and is named by :attr:`Table.env_dropped_rules` rather than emitted half-broken.
LOOPBACK_EXPANSION = {
    "127.0.0.0/8": ("127.0.0.1", "localhost"),
    "::1/128": ("::1", "localhost"),
}


def _expands_to_env_entries(rule: "Rule") -> tuple[str, ...]:
    """What this ``direct`` rule can be written as in a child's environment."""
    if rule.kind != "cidr":
        return (rule.value,)
    return LOOPBACK_EXPANSION.get(rule.value, ())

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
class Node:
    """One engine-side member (a real proxy node, not a forward proxy).

    The table owns routing *and*, since 2026-10-12, its own membership: a rule
    may now point at a node or a group, and the compiler emits them into the
    engine config.  The two consequences of that are enforced here rather than
    documented:

    * **No secret ever lands in this object.**  ``password``/``uuid``/``username``
      may only be declared as ``*_env`` references, so the file users hand-edit
      keeps names and ``.env`` keeps the values; the compiler resolves them at
      emit time and nothing else does.
    * **A child cannot express it.**  ``ss://``/``vmess://`` are not what
      ``HTTPS_PROXY`` means, so rules pointing here are engine-only: the
      in-process resolver skips them, and :attr:`Table.engine_only_rules` and
      :attr:`Table.env_dropped_rules` both say so out loud.
    """

    name: str
    type: str
    server: str
    port: int
    fields: dict[str, Any] = field(default_factory=dict)
    #: Secret fields given **inline** in config.yaml.  Accepted with a warning —
    #: refusing them would make migrating a real pool an outage — but the settings
    #: write path rejects them outright.
    secrets: dict[str, Any] = field(default_factory=dict)
    #: Secret fields given as ``<field>_env``: field name → environment variable
    #: name.  Only names live here; the value is read at compile time.
    secret_env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Group:
    name: str
    type: str
    members: tuple[str, ...]
    #: ``True`` for ``include: '*'`` — every node in the table.
    all_members: bool = False
    interval: int = 0


@dataclass(frozen=True)
class Table:
    rules: tuple[Rule, ...] = ()
    proxies: dict[str, str] = field(default_factory=dict)
    mirrors: dict[str, str] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    engine_enabled: bool = False
    engine_binary: str = ""
    engine_mixed_port: int = 0
    #: The engine's own workdir (the directory holding its ``config.yaml``), used
    #: when the pool stays outside the table.  A node pool cannot be *rendered*
    #: into a generated config through a key like this — measured on this box with
    #: mihomo v1.19.32 (2026-10-12 08:46) that a top-level ``include`` is ignored
    #: (``mihomo -t`` answers ``proxy [x] not found``, rc=1) — so
    #: :mod:`agent.egress.mihomo_config` refuses to pretend otherwise, and
    #: :mod:`agent.egress.engine` starts the engine on this directory instead.
    #: Membership of one's own now belongs in ``nodes``/``groups`` above.
    engine_include: tuple[str, ...] = ()
    #: Resolvers for the engine to dial its own upstreams with, e.g.
    #: ``{default_nameserver: ['223.5.5.5'], nameserver: ['223.5.5.5']}``.  Left empty
    #: on purpose: the compiler refuses to invent public DNS for somebody's machine.
    engine_dns: dict[str, Any] = field(default_factory=dict)

    #: 用户 10-13 改口的范围：把**整机**的出口交给引擎（接管系统代理 + 表生成的 PAC）。
    #: 默认 false —— 没写这张表的人不该被改了系统设置；且这一格可精确回退。
    system_proxy_enabled: bool = False

    #: Non-empty when the config could not be read at all, which is a *different*
    #: state from "no table declared": a caller that must not run unrouted (the
    #: external-offload child) has to be able to tell them apart.
    unreadable: str = ""

    @property
    def active(self) -> bool:
        return bool(self.rules)

    @property
    def engine_names(self) -> frozenset[str]:
        """Exit names only the engine can act on — nodes and groups."""
        return frozenset(self.nodes) | frozenset(self.groups)

    def _rule_is_engine_only(self, rule: "Rule") -> bool:
        return rule.kind == "geosite" or (
            rule.via.startswith("proxy:") and rule.via[len("proxy:") :] in self.engine_names
        )

    @property
    def engine_only_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if self._rule_is_engine_only(r))

    @property
    def env_dropped_rules(self) -> tuple[Rule, ...]:
        """Rules the subprocess environment cannot carry.

        Three distinct reasons, reported together because the child's answer is the
        same (that route simply does not reach it):

        * the catch-all is ``direct``, so a ``proxy:`` rule above it has no exit
          to name;
        * the rule's exit is a **node or group** — engine-only, because
          ``HTTPS_PROXY`` cannot carry ``ss://``/``vmess://`` semantics;
        * the rule is an **address range** that is not loopback — ``NO_PROXY`` has
          no range vocabulary, and ``urllib``/``httpx`` ignore a CIDR entry outright
          (see :data:`LOOPBACK_EXPANSION`), so that traffic goes to the catch-all.
        """
        if not self.active:
            return ()
        if self.rules[-1].via != DIRECT:
            base = tuple(
                r for r in self.rules if self._rule_is_engine_only(r) and r.kind != "geosite"
            )
        else:
            base = tuple(
                r
                for r in self.rules
                if r.via.startswith("proxy:") and r.kind != "geosite" and r.kind != "all"
            )
        ranges = tuple(
            r
            for r in self.rules
            if r.via == DIRECT and not _expands_to_env_entries(r)
        )
        return tuple(dict.fromkeys(base + ranges))

    def proxy_url(self, name: str) -> str:
        try:
            return self.proxies[name]
        except KeyError:
            if name in self.engine_names:
                raise EgressConfigError(
                    f"proxy:{name} is a node or group — only the engine can act on it, and a URL "
                    f"cannot be derived from it.  To let spawned tools and our own HTTP clients use "
                    f"it, declare the engine's front door (http://127.0.0.1:<mixed_port>) under "
                    f"network.egress.proxies and point those rules at it."
                ) from None
            raise EgressConfigError(
                f"network.egress.rules references proxy:{name} but "
                f"network.egress.proxies has no such entry (declared: "
                f"{sorted(self.proxies) or 'none'})"
            ) from None

    def resolves(self, name: str) -> bool:
        """Does this exit name exist at all — as a URL exit or an engine-side one?"""
        return name in self.proxies or name in self.engine_names


@dataclass(frozen=True)
class Decision:
    proxy_url: Optional[str]
    rule: Rule


def _compile_rule(entry: Any, index: int) -> Rule:
    where = f"network.egress.rules[{index}]"
    if not isinstance(entry, dict):
        raise EgressConfigError(f"{where} must be a mapping, got {type(entry).__name__}")
    match = str(entry.get("match", "")).strip().lower()
    via_raw = str(entry.get("via", "")).strip()
    via = via_raw.lower()
    if not match:
        raise EgressConfigError(f"{where}.match is required")
    if not via:
        raise EgressConfigError(f"{where}.via is required ('direct' or 'proxy:<name>')")
    if via == DIRECT:
        kind = _matcher_kind(match, where)
        return Rule(match=match, via=via, kind=kind, value=_matcher_value(match, kind))
    if via.startswith("proxy:"):
        # Only the *keyword* is case-insensitive.  An exit name is a name — the
        # engine's own groups are conventionally uppercase (``POOL``), and
        # lowercasing it here would make a declared exit unreachable.
        name = via_raw[len("proxy:") :].strip()
        if not name:
            raise EgressConfigError(f"{where}.via '{via_raw}' names no proxy")
        kind = _matcher_kind(match, where)
        return Rule(
            match=match, via=f"proxy:{name}", kind=kind, value=_matcher_value(match, kind)
        )
    raise EgressConfigError(
        f"{where}.via must be 'direct' or 'proxy:<name>', got '{via_raw}'"
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


#: Engine-side member types we will compile.  Anything else is refused rather
#: than passed through: a typo here means a node that silently never dials.
_NODE_TYPES: dict[str, tuple[str, ...]] = {
    "ss": ("cipher",),
    "vmess": ("uuid",),
    "trojan": ("password",),
    "vless": ("uuid",),
    "socks5": (),
    "http": (),
}
#: Fields that carry a secret; each may be given inline or as ``<field>_env``.
_SECRET_FIELDS = ("password", "uuid", "username")
#: Non-secret per-node fields forwarded to the engine untouched.
_NODE_PASSTHROUGH = frozenset(
    {
        "udp",
        "tls",
        "servername",
        "sni",
        "skip-cert-verify",
        "alpn",
        "client-fingerprint",
        "packet-encoding",
        "alterId",
        "cipher",
        "network",
        "ws-opts",
        "h2-opts",
        "grpc-opts",
        "reality-opts",
        "flow",
        "obfs",
        "obfs-param",
        "plugin",
        "plugin-opts",
    }
)
_GROUP_TYPES = ("url-test", "select", "fallback")

_cleartext_secret_warned: set[str] = set()
#: Engine fields we do not enumerate get one warning per field name, not one per node.
_unknown_node_fields_warned: set[str] = set()


def _parse_node(name: str, entry: Any) -> Node:
    where = f"network.egress.nodes.{name}"
    if not isinstance(entry, dict):
        raise EgressConfigError(f"{where} must be a mapping with 'type', 'server' and 'port'")
    kind = str(entry.get("type", "")).strip().lower()
    if kind not in _NODE_TYPES:
        raise EgressConfigError(
            f"{where}.type '{kind or '(none)'}' is unsupported; expected one of "
            f"{', '.join(sorted(_NODE_TYPES))}"
        )
    server = str(entry.get("server", "")).strip()
    if not server:
        raise EgressConfigError(f"{where}.server is required")
    try:
        port = int(entry.get("port", 0))
    except (TypeError, ValueError):
        raise EgressConfigError(f"{where}.port must be a number") from None
    if not 0 < port < 65536:
        raise EgressConfigError(f"{where}.port {port} is not a port")

    fields: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    secret_env: dict[str, str] = {}
    for required in _NODE_TYPES[kind]:
        if required in _SECRET_FIELDS:
            continue
        if not entry.get(required):
            raise EgressConfigError(f"{where}.{required} is required for type '{kind}'")
        fields[required] = entry[required]

    for field_name in _SECRET_FIELDS:
        env_name = str(entry.get(f"{field_name}_env", "")).strip()
        inline = entry.get(field_name)
        if env_name and inline not in (None, ""):
            raise EgressConfigError(f"{where} declares both '{field_name}' and '{field_name}_env'; pick one")
        if env_name:
            secret_env[field_name] = env_name
        elif inline not in (None, ""):
            if field_name in _NODE_TYPES[kind]:
                # Accepted so a real pool can be migrated without an outage, said
                # out loud once, and rejected on the GUI write path.
                if where not in _cleartext_secret_warned:
                    _cleartext_secret_warned.add(where)
                    logging.getLogger(__name__).warning(
                        "network.egress.nodes.%s keeps a cleartext '%s' in config.yaml — the file "
                        "users hand-edit; declare %s_env and put the value in the root .env instead",
                        name,
                        field_name,
                        field_name,
                    )
                secrets[field_name] = inline
            else:
                fields[field_name] = inline

    allowed = {"type", "server", "port", "name"} | set(_NODE_PASSTHROUGH) | {
        f for r in _NODE_TYPES[kind] for f in (r, f"{r}_env")
    } | {f for s in _SECRET_FIELDS for f in (s, f"{s}_env")}
    # Unknown fields are **passed through with one warning each**, not refused.
    # Refusing them looked like typo protection until a real pool met it: this
    # box's 46-node pool carries `alterId` (and other engine fields we never
    # enumerated), so a refusal meant the user's own membership could not be put
    # in the table at all — worse than the typo it was preventing.  The engine is
    # the authority on its own fields, and it is already consulted twice: `mihomo -t`
    # before we start, and a probe with a known answer after we do.
    for key in entry:
        key = str(key)
        if key not in allowed and key not in _NODE_PASSTHROUGH and key not in fields:
            if key not in _unknown_node_fields_warned:
                _unknown_node_fields_warned.add(key)
                logging.getLogger(__name__).warning(
                    "network.egress.nodes.%s has field '%s' that this table does not know — passed "
                    "through to the engine untouched (it will be mihomo -t and the live probe that "
                    "judge it, not us)",
                    name,
                    key,
                )
            fields[key] = entry[key]

    for key in _NODE_PASSTHROUGH:
        if key in entry:
            fields[key] = entry[key]
    return Node(name=name, type=kind, server=server, port=port, fields=fields,
                secrets=secrets, secret_env=secret_env)


def _parse_group(name: str, entry: Any, node_names: frozenset[str]) -> Group:
    where = f"network.egress.groups.{name}"
    if not isinstance(entry, dict):
        raise EgressConfigError(f"{where} must be a mapping with 'type' and 'include'")
    kind = str(entry.get("type", "")).strip()
    if kind not in _GROUP_TYPES:
        raise EgressConfigError(
            f"{where}.type '{kind or '(none)'}' is unsupported; expected one of {', '.join(_GROUP_TYPES)}"
        )
    include = entry.get("include")
    interval = entry.get("interval", 0) or 0
    if include == "*":
        if not node_names:
            raise EgressConfigError(f"{where}.include is '*' but network.egress.nodes is empty")
        return Group(name=name, type=kind, members=tuple(sorted(node_names)), all_members=True,
                     interval=int(interval))
    if not isinstance(include, Iterable) or isinstance(include, (str, bytes)):
        raise EgressConfigError(f"{where}.include must be a list of node names or '*'")
    members = tuple(str(m).strip() for m in include)
    if not members:
        raise EgressConfigError(f"{where}.include is empty")
    unknown = [m for m in members if m not in node_names]
    if unknown:
        raise EgressConfigError(
            f"{where}.include names {', '.join(unknown)} which is not under network.egress.nodes "
            f"(declared: {', '.join(sorted(node_names)) or 'none'})"
        )
    return Group(name=name, type=kind, members=members, interval=int(interval))


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

    nodes_raw = raw.get("nodes") or {}
    if not isinstance(nodes_raw, dict):
        raise EgressConfigError("network.egress.nodes must be a mapping of name -> node")
    nodes = {str(name): _parse_node(str(name), entry) for name, entry in nodes_raw.items()}

    groups_raw = raw.get("groups") or {}
    if not isinstance(groups_raw, dict):
        raise EgressConfigError("network.egress.groups must be a mapping of name -> group")
    groups = {
        str(name): _parse_group(str(name), entry, frozenset(nodes))
        for name, entry in groups_raw.items()
    }
    shadowed = sorted(set(nodes) & set(groups))
    if shadowed:
        raise EgressConfigError(
            f"network.egress.nodes and .groups both declare: {', '.join(shadowed)} — an exit name "
            "must resolve one way, not two"
        )

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
            probe = Table(proxies=proxies, nodes=nodes, groups=groups)
            name = rule.via[len("proxy:") :]
            if not probe.resolves(name):
                probe.proxy_url(name)  # raises with the "not declared" wording

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

    sysp_raw = raw.get("system_proxy") or {}
    if not isinstance(sysp_raw, dict):
        raise EgressConfigError("network.egress.system_proxy must be a mapping ({enabled: true|false})")

    return Table(
        rules=rules,
        proxies=proxies,
        mirrors=mirrors,
        nodes=nodes,
        groups=groups,
        engine_enabled=bool(engine_raw.get("enabled", False)),
        engine_binary=str(engine_raw.get("binary", "") or "").strip(),
        engine_mixed_port=mixed_port,
        engine_include=_parse_include(engine_raw),
        engine_dns=_parse_dns(engine_raw),
        system_proxy_enabled=bool(sysp_raw.get("enabled", False)),
    )


def _parse_dns(engine_raw: dict) -> dict[str, Any]:
    """`engine.dns` — passed through to the engine, keys normalised to its spelling."""
    dns_raw = engine_raw.get("dns")
    if dns_raw in (None, {}):
        return {}
    if not isinstance(dns_raw, dict):
        raise EgressConfigError("network.egress.engine.dns must be a mapping")
    out: dict[str, Any] = {}
    for key, value in dns_raw.items():
        key = str(key).strip()
        if not key:
            raise EgressConfigError("network.egress.engine.dns contains an empty key")
        if isinstance(value, (list, tuple)):
            value = [str(v).strip() for v in value if str(v).strip()]
        out[key.replace('_', '-')] = value
    return out


def _parse_include(engine_raw: dict) -> tuple[str, ...]:
    include = engine_raw.get("include_files", []) or []
    if isinstance(include, (str, bytes)) or not isinstance(include, (list, tuple)):
        raise EgressConfigError(
            "network.egress.engine.include_files must be a list of paths — it is the "
            "place an engine's own node pool lives, not inline proxy entries"
        )
    out: list[str] = []
    for item in include:
        text = str(item).strip()
        if not text:
            raise EgressConfigError(
                "network.egress.engine.include_files contains an empty path"
            )
        out.append(text)
    return tuple(out)


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
        return _report_unrepresentable(
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
        # Marked, not just warned: a caller that must refuse to run unrouted
        # (the offload child) reads Table.unreadable; the spawn path keeps working.
        _TABLE_CACHE = Table(unreadable=f"{type(exc).__name__}: {exc}")
        return _TABLE_CACHE
    _TABLE_CACHE = _report_unrepresentable(parse_table(raw))
    return _TABLE_CACHE


def _report_unrepresentable(table: Table) -> Table:
    """Name what each projection layer cannot honor, instead of quietly dropping it.

    Two asymmetries are structural, not bugs, and both would otherwise degrade in
    silence: ``geosite:`` matchers need the engine's data files, and a table whose
    catch-all is ``direct`` has no single exit for a spawned child to be told
    about.  In-process routing is correct in both cases — which is precisely why
    the loss must be reported at load (裁定 94: only-visible-in-logs is not yet
    visible to the user, so this text also has to reach the status surface).
    """
    log = logging.getLogger(__name__)
    if table.engine_only_rules and not table.engine_enabled:
        log.warning(
            "network.egress: %d rule(s) %s are engine-only and do nothing until "
            "network.egress.engine.enabled is true — traffic matching them falls "
            "through to the catch-all rule",
            len(table.engine_only_rules),
            [r.match for r in table.engine_only_rules],
        )
    if table.env_dropped_rules:
        # Three different reasons reach the same child-side outcome; one sentence for
        # all of them would name the wrong rules and send the next reader to fix the
        # wrong thing.
        ranges = [r for r in table.env_dropped_rules if r.via == DIRECT]
        exits = [r for r in table.env_dropped_rules if r.via != DIRECT]
        if exits:
            if table.rules[-1].via == DIRECT:
                log.warning(
                    "network.egress: %d rule(s) %s route to a named exit, but the catch-all "
                    "rule is direct — a spawned subprocess cannot be told 'one exit except "
                    "these hosts' in that shape, so those rules apply in-process only and "
                    "are dropped from the child's environment",
                    len(exits),
                    [r.match for r in exits],
                )
            else:
                log.warning(
                    "network.egress: %d rule(s) %s point at a node or group — ss:// and vmess:// "
                    "have no URL form, so a spawned subprocess cannot be told about them and the "
                    "engine is the only thing that will honor those rules",
                    len(exits),
                    [r.match for r in exits],
                )
        if ranges:
            log.warning(
                "network.egress: %d rule(s) %s are address ranges — NO_PROXY has no range "
                "vocabulary and urllib/httpx ignore a CIDR entry outright, so a spawned "
                "subprocess sends that traffic to the catch-all exit instead; loopback "
                "(127.0.0.0/8, ::1/128) is expanded and is not in this list",
                len(ranges),
                [r.match for r in ranges],
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
    if table._rule_is_engine_only(rule):
        # The catch-all is an engine-side member: there is no URL any caller here
        # can be handed, and inventing one would route traffic somewhere the
        # table never pointed.
        return None
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
        if table._rule_is_engine_only(rule):
            # geosite matchers need the engine's data files, and a node/group exit
            # has no URL to hand out: both fall through to the next rule instead
            # of guessing, and both are named by engine_only_rules.
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
