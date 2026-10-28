"""The node the user configured in Settings → VPN, as a proxy URL.

Doc-level fact (measured 2026-10-28 on this machine): the VPN chip owns a node pool and
runs a Clash-style engine whose `mixed-port` (7895 here) is the one address every client
inside Plobi can share. Legs that must reach Google read this and nothing else — the user
配置节点一次，哪条腿需要就走它（用户 10-28 的原话：「整个 Plobi 和网络相关都可以利用」）。

The hub's own client runs with `trust_env: false` (2026-10-14: a dead local proxy poisoned
every upstream), so `HTTP(S)_PROXY` cannot reach these legs; this resolver is the way in.

Returns `(url, reason)`: `("", "no_proxy")` when no node is configured,
`("", "proxy_unreachable")` when the port is configured but nothing is listening, and
`(url, "ok")` otherwise. Never guesses a port, never invents a node.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path


def proxy_url() -> tuple[str, str]:
    home = os.environ.get("PLOBI_HOME", "").strip()
    if not home:
        return "", "no_proxy"
    cfg = Path(home) / "egress" / "config.yaml"
    if not cfg.is_file():
        return "", "no_proxy"
    port = 0
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key.strip() in {"mixed-port", "port", "socks-port"} and value.strip().isdigit():
                port = int(value.strip())
                break
    except OSError:
        return "", "no_proxy"
    if not port:
        return "", "no_proxy"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass
    except OSError:
        return "", "proxy_unreachable"
    return f"http://127.0.0.1:{port}", "ok"
