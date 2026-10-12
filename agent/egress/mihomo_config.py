"""Compile the egress table into a mihomo (Clash Meta) configuration.

The table stays the truth; this is one *projection* of it, used only when
``network.egress.engine.enabled`` is true.  Nothing here downloads, starts or
monitors a binary — that is :mod:`agent.egress.engine` — and
:func:`verify_with_binary` is the honest "did the engine accept this?" path.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

from agent.egress.rules import EgressConfigError, Table

_SCHEME_TO_TYPE = {"http": "http", "https": "http", "socks5": "socks5", "socks5h": "socks5h"}
_DEFAULT_PORT = {"http": 80, "socks5": 1080, "socks5h": 1080}


def compile_config(
    table: Table,
    *,
    mixed_port: int | None = None,
    external_controller: str = "",
    secret: str = "",
    log_level: str = "info",
) -> str:
    """Render the table as mihomo YAML, preserving rule order."""
    if not table.active:
        raise EgressConfigError(
            "no egress rules to compile — network.egress.rules is empty, so there "
            "is nothing for the engine to enforce"
        )
    port = table.engine_mixed_port if mixed_port is None else mixed_port
    if not isinstance(port, int) or port <= 0:
        raise EgressConfigError(
            f"the engine needs an explicit listen port, got {port!r} — set "
            f"network.egress.engine.mixed_port (no default is invented here)"
        )

    declared: set[str] = set()
    proxies: list[dict[str, Any]] = []
    for name in sorted(table.proxies):
        entry = _proxy_entry(name, table.proxies[name])
        declared.add(name)
        proxies.append(entry)

    rules: list[str] = []
    for rule in table.rules:
        target = "DIRECT" if rule.via == "direct" else rule.via[len("proxy:") :]
        if target != "DIRECT" and target not in declared:
            raise EgressConfigError(
                f"rule '{rule.match}' targets '{target}', which is not declared under "
                f"network.egress.proxies"
            )
        rules.append(_rule_line(rule.kind, rule.value, target))

    document: dict[str, Any] = {
        "mixed-port": port,
        "mode": "rule",
        "log-level": log_level,
        "allow-lan": False,
        "proxies": proxies,
        "rules": rules,
    }
    if external_controller:
        document["external-controller"] = external_controller
    if secret:
        document["secret"] = secret
    if table.engine_include:
        raise EgressConfigError(
            f"network.egress.engine.include_files names {len(table.engine_include)} file(s), and "
            f"this compiler will not emit them: mihomo v1.19.32 on this box (measured 2026-10-12 "
            f"08:46) **ignores** a top-level 'include' key -- 'mihomo -t' answers "
            f"'proxy [<name>] not found' and exits 1 for a rule whose node lives only in the "
            f"referenced file -- so rendering it would hand the supervision slice a config the "
            f"engine refuses, or an engine running with no upstreams at all.  A node pool is the "
            f"engine's own workdir: see agent.egress.engine, which starts mihomo with -d set to the "
            f"directory holding that file."
        )

    _refuse_self_reference(proxies, port)

    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


#: Hosts that mean "this machine, and specifically the socket we are about to
#: bind" — `allow-lan: false` makes the engine listen on loopback.
_SELF_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _refuse_self_reference(proxies: list[dict[str, Any]], port: int) -> None:
    """Reject a table that tells the engine to hand traffic to itself.

    The same `(host, port)` is read two different ways by two consumers: a
    spawned process sees it as "who to hand my traffic to" (correct — that is the
    engine's listener), while the compiled config sees it as "where to forward"
    (fatal — the engine forwards into its own accept queue).  Reproduced on this
    box with v1.19.32 (2026-10-11 16:40): the rendered config passes
    ``mihomo -t`` with rc=0 "test is successful", then at runtime every request
    dies with ``dial tcp 127.0.0.1:<port>: bind: Only one usage of each socket
    address``, and the churn pushed this box's TimeWait to ~15.6k ephemeral
    sockets.  **A validation that cannot see it is why the compiler must refuse
    it** — otherwise the supervision slice starts a dead loop as its first act.
    """
    for entry in proxies:
        if entry["port"] == port and str(entry["server"]).lower() in _SELF_HOSTS:
            raise EgressConfigError(
                f"network.egress.proxies.{entry['name']} points at "
                f"{entry['server']}:{port}, which is the engine's own "
                f"mixed-port — the compiled config would route traffic to the engine "
                f"itself.  mihomo validates it anyway (observed on this box: rc=0, "
                f'"test is successful"), then every connection dies at runtime.  '
                f"Give the engine a listen port of its own and point this exit at "
                f"whatever really sits upstream, or drop the engine projection."
            )


def _proxy_entry(name: str, url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    kind = _SCHEME_TO_TYPE.get(parsed.scheme.lower())
    if kind is None:
        raise EgressConfigError(f"proxy '{name}' has unsupported scheme '{parsed.scheme}'")
    if not parsed.hostname:
        raise EgressConfigError(f"proxy '{name}' URL '{url}' has no host")
    entry: dict[str, Any] = {
        "name": name,
        "type": kind,
        "server": parsed.hostname,
        "port": parsed.port or _DEFAULT_PORT[kind],
    }
    if parsed.username:
        entry["username"] = unquote(parsed.username)
    if parsed.password:
        entry["password"] = unquote(parsed.password)
    return entry


def _rule_line(kind: str, value: str, target: str) -> str:
    if kind == "all":
        return f"MATCH,{target}"
    if kind == "exact":
        return f"DOMAIN,{value},{target}"
    if kind == "suffix":
        return f"DOMAIN-SUFFIX,{value},{target}"
    if kind == "geosite":
        return f"GEOSITE,{value[len('geosite:') :]},{target}"
    if kind == "cidr":
        version = "IP-CIDR6" if ":" in value else "IP-CIDR"
        try:
            net = ipaddress.ip_network(value)
            version = "IP-CIDR6" if net.version == 6 else "IP-CIDR"
        except ValueError:
            pass
        return f"{version},{value},{target},no-resolve"
    raise EgressConfigError(f"cannot compile matcher kind '{kind}' for the engine")


#: mihomo validates ``<dir>/config.yaml`` — it does not take a config path.
#: Verified on this box (Mihomo Meta v1.19.32): pointing ``-t`` at a directory
#: holding only ``mihomo-config.yaml`` **creates** a 16-byte ``config.yaml`` and
#: reports "successful", so a caller that ignores the filename gets a green
#: verdict for bytes the engine never read.
CONFIG_BASENAME = "config.yaml"


def verify_with_binary(binary: str, config_path: str) -> tuple[str, str]:
    """Ask the engine itself whether it accepts the rendered config.

    Returns ``("UNVERIFIED", why)`` rather than pretending success when no
    binary is available — a compiler that was never accepted by its target is
    not a passing test.  It also refuses to report a verdict for a file the
    engine would not read (see :data:`CONFIG_BASENAME`), and confirms from the
    engine's own output which path it validated.

    Two limits measured on this box (v1.19.32) that keep this from meaning
    "the config works":
    * ``GEOSITE`` rules make ``-t`` **go to the network** when the directory has
      no ``GeoSite.dat`` — observed 16:40:40 → 16:40:50, then ``test failed``.
      Ship or copy the geo data into the workdir, otherwise the verdict depends
      on this machine's reachability at that moment.
    * ``-t`` validates syntax and rule resolution only.  It accepts a
      self-referential config (see :func:`_refuse_self_reference`), so a VERIFIED
      verdict is never evidence that the engine can carry traffic.
    """
    if not binary:
        return "UNVERIFIED", "network.egress.engine.binary is empty"
    if not os.path.isfile(config_path):
        return "UNVERIFIED", f"no config at '{config_path}' to check"
    if os.path.basename(config_path) != CONFIG_BASENAME:
        return (
            "UNVERIFIED",
            f"mihomo validates <dir>/{CONFIG_BASENAME}, but the file given is "
            f"'{os.path.basename(config_path)}' — rendering it under that name would let "
            f"-t check whatever else is in that directory (it creates an empty "
            f"{CONFIG_BASENAME} when none exists) and still report success",
        )
    path = binary if os.path.isabs(binary) else shutil.which(binary)
    if not path or not os.path.isfile(path):
        return "UNVERIFIED", f"engine binary not found at '{binary}'"
    workdir = os.path.dirname(config_path) or "."
    try:
        proc = subprocess.run(
            [path, "-t", "-d", workdir],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "UNVERIFIED", f"could not run '{path}': {exc}"
    detail = (proc.stdout + proc.stderr).strip()
    checked = os.path.normcase(os.path.abspath(config_path))
    named = set()
    for token in detail.replace("\\", "/").split():
        candidate = token.strip("\"'")
        if candidate.lower().endswith(CONFIG_BASENAME):
            named.add(os.path.normcase(os.path.abspath(candidate)))
    if proc.returncode != 0:
        return "REJECTED", detail
    if checked not in named:
        return (
            "UNVERIFIED",
            f"-t exited 0 but never named this file in its output, so the verdict belongs "
            f"to some other config: {detail[-200:]}",
        )
    return "VERIFIED", detail

