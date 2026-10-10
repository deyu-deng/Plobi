"""Compile the egress table into a mihomo (Clash Meta) configuration.

The table stays the truth; this is one *projection* of it, used only when
``network.egress.engine.enabled`` is true.  Nothing here downloads or starts a
binary — see :func:`verify_with_binary` for the honest "did the engine accept
this?" path.
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

    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


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


def verify_with_binary(binary: str, config_path: str) -> tuple[str, str]:
    """Ask the engine itself whether it accepts the rendered config.

    Returns ``("UNVERIFIED", why)`` rather than pretending success when no
    binary is available — a compiler that was never accepted by its target is
    not a passing test.
    """
    if not binary:
        return "UNVERIFIED", "network.egress.engine.binary is empty"
    path = binary if os.path.isabs(binary) else shutil.which(binary)
    if not path or not os.path.isfile(path):
        return "UNVERIFIED", f"engine binary not found at '{binary}'"
    if not os.path.isfile(config_path):
        return "UNVERIFIED", f"no config at '{config_path}' to check"
    try:
        proc = subprocess.run(
            [path, "-t", "-d", os.path.dirname(config_path) or "."],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "UNVERIFIED", f"could not run '{path}': {exc}"
    detail = (proc.stdout + proc.stderr).strip()
    return ("VERIFIED", detail) if proc.returncode == 0 else ("REJECTED", detail)
