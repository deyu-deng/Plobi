"""Plobi environment doctor — chatlog / aigw / DingTalk / profile (C line).

One command, one status table. Exit 0 when nothing is red — ``deferred`` rows
do NOT fail the run (see :data:`DEFERRED`).
Does not send DingTalk unless ``--send-test``. Never prints secrets.

    python scripts/plobi/doctor.py
    python scripts/plobi/doctor.py --json
    python scripts/plobi/doctor.py --send-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
# Ensure plobi_cli is importable when running `python scripts/plobi/doctor.py`
# from the repo root without the project on PYTHONPATH. ``--app-info`` reads the
# device pairing store through ``plobi_cli.dashboard_auth.devices`` (lazy), so a
# fresh checkout can report handset access without installing anything.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
AIGW_CONFIG_CANDIDATES = (
    REPO_ROOT / "aigw" / "config.yaml",
    REPO_ROOT / "aigw" / "config.example.yaml",
)
CHATLOG_HEALTH_PATHS = ("/health", "/api/v1/health")
DEFAULT_CHATLOG_URL = "http://127.0.0.1:5030"
DEFAULT_AIGW_PORT = 8000
NORTH_STAR = "plobi_north_star"

GREEN, YELLOW, RED = "green", "yellow", "red"

# ---------------------------------------------------------------------------
# R-047 — a fourth status: DEFERRED ("后置，不是坏了").
#
# The product owner explicitly postponed the WeChat collection stack:
# 「先不要做采集的功能了 … 等应用打磨得比较完整了，我才会提供数据的。」
# Reporting those rows red was a *false* red — it destroyed the meaning of
# "red == something broke" and drowned the two rows that genuinely need fixing
# (``dingtalk`` missing its webhook secret, ``north_star`` not wired into a
# toolset).
#
# This is NOT the LAN row's trick (see ``collect_app_pairing_info`` above):
# LAN is dropped from the table entirely because it is opt-in read-out. A
# deferred capability stays in the table, still gets probed, and still goes
# GREEN the moment it is really live — deferral is a product decision about
# priority, not a permanent disable. Only a *failed* probe is downgraded from
# red to deferred.
#
# ``DEFERRED`` is 8 chars, so it fills the existing ``{'color':<8}`` column
# exactly and the table keeps its alignment.
# ---------------------------------------------------------------------------
DEFERRED = "deferred"

# One line per deferred capability: what it would take to switch it on *on this
# machine*. These must never name a PowerShell launcher — the Windows box was
# decommissioned on 2026-09-27 and macOS is now the only true source, so advice
# like "run scripts/chatlog_server.ps1" is unactionable and misleading. Every
# hint below was verified against this checkout.
DEFERRED_ENABLE_HINTS: dict[str, str] = {
    "chatlog": (
        "install Go (absent on this machine), `cd tools/chatlog && "
        "go build -o bin/chatlog ./cmd/chatlog`, log into WeChat, export "
        "CHATLOG_DATA_KEY, serve on :5030"
    ),
    "aigw": (
        "Plobi starts this service itself when it is needed — there is nothing "
        "to run by hand."
    ),
    "collect": (
        "do the first-run blacklist review so $PLOBI_HOME/plobi/chatlog.json "
        "exists with mode=blacklist (needs chatlog live first)"
    ),
    "whitelist_eff": (
        "`uv run python scripts/plobi/whitelist_report.py` once the collector "
        "has real traffic to sweep"
    ),
}

# The four R-047 ids. Kept as a set so ``worst_exit`` and any future consumer
# share one definition of "which capabilities are deferred".
DEFERRED_CAPABILITY_IDS = frozenset(DEFERRED_ENABLE_HINTS)


def deferred_row(check_id: str, observed: str, **extra: Any) -> dict[str, Any]:
    """Build a DEFERRED row: the real probe result + why it is not a fault.

    ``observed`` is the honest outcome of the probe that was still performed
    (port refused, file absent, HTTP 503, …). It is never elided — a deferred
    row that hides what it found is indistinguishable from a skipped check.
    """
    hint = DEFERRED_ENABLE_HINTS[check_id]
    # "Enable:" promised *steps* in front of a hint that is frequently a
    # statement — the aigw row reads "…Plobi starts this service itself when it
    # is needed — there is nothing to run by hand", so an imperative label
    # contradicted the sentence after it. The prefix is an explanation of the
    # path back to green, which is true for both shapes (real steps for chatlog,
    # a statement for the app-owned services). Wording is byte-pinned to
    # apps/desktop/electron/deferred-sidecars.ts — change both together.
    detail = (
        f"{observed} — deferred by R-047 (postponed, not a fault; turns green "
        f"on its own once live). How it comes back: {hint}"
    )
    row: dict[str, Any] = {"id": check_id, "color": DEFERRED, "detail": detail}
    row.update(extra)
    return row

# Below this share of active traffic being captured by the collector counts
# as "whitelist is leaking chat" — spec from
# ``Docs/PROMPT-CHATLOG-WHITELIST-REPORT.md`` ("低于 10% 标黄").
# See scripts/plobi/whitelist_report.py.
WHITELIST_EFFICIENCY_YELLOW = 0.10
WHITELIST_PROPOSAL_FILENAME = "whitelist_proposal.json"
WHITELIST_PROPOSAL_FRESH_HOURS = 36


def plobi_home() -> Path:
    override = (os.environ.get("PLOBI_HOME") or "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_plobi_home

        return get_plobi_home()
    except Exception:
        return Path.home() / ".plobi"


# ---------------------------------------------------------------------------
# WP-H1-LAN — handset access info for the Plobi App (tablet / phone / any LAN
# client). Kept out of the regular green/yellow/red table because it's an
# *opt-in* read-out, not a health check: a user who never opens PLOBI_LAN should
# never see a red row for "LAN off". The credential is no longer a file here —
# each device pairs for its own token, so this reads the pairing store itself
# through ``plobi_cli.dashboard_auth.devices`` (single source for where those
# records live; the desktop writes the same files).
# ---------------------------------------------------------------------------
LAN_BIND_HOST_DEFAULT = "0.0.0.0"
LAN_BIND_PORT_DEFAULT = 8787


def _pairing_state(home: Path) -> dict[str, Any]:
    """Device count + writability for this home's pairing store.

    Writability is what the backend's LAN gate asks before binding, so reporting
    it here answers "why did my LAN bind silently fall back to loopback" without
    the operator having to read the log.
    """
    try:
        from plobi_cli.dashboard_auth.devices import DeviceStore

        store = DeviceStore(home)
        return {
            "pairing_store_writable": store.root_writable(),
            "paired_devices": len(store.list_devices()),
        }
    except Exception as exc:  # pragma: no cover — diagnostic read-out, never fatal
        return {"pairing_store_writable": False, "paired_devices": 0,
                "pairing_error": str(exc)}


def _resolve_plobi_lan(env: dict[str, str] | None = None) -> str:
    """Return the effective PLOBI_LAN setting, honouring $PLOBI_HOME/.env.

    Same logic as the Electron ``resolveLanMode``: literal ``"1"`` only.
    A non-truthy shell value wins over a truthy .env entry because the
    shell is the more recent intent.
    """
    effective = env if env is not None else os.environ
    inherited = (effective.get("PLOBI_LAN") or "").strip()
    if inherited:
        return inherited
    # Fall back to the user's .env (single-line `PLOBI_LAN=1`).
    parsed = load_env_file((plobi_home()) / ".env")
    return (parsed.get("PLOBI_LAN") or "").strip()


def _lan_ipv4_addresses() -> list[str]:
    """Best-effort list of LAN-suitable IPv4 addresses.

    Skips loopback (127.x) and link-local (169.254.x) so the output is
    immediately paste-able into a tablet. Uses
    :func:`socket.gethostbyname_ex` against the local hostname first
    (cheap, often correct) then falls back to a UDP-socket trick that
    does NOT actually open a connection — standard idiom for getting the
    primary NIC's outbound IP without an external probe.

    Returns an empty list on failure rather than raising — doctor must
    keep working on weird networks (containers, WSL, no NIC).
    """
    import socket

    addresses: list[str] = []
    seen: set[str] = set()

    def _accept(ip: str) -> bool:
        return (
            ip
            and not ip.startswith("127.")
            and not ip.startswith("169.254.")
            and ":" not in ip
        )

    # 1. Hostname lookup — covers the common case.
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if _accept(ip) and ip not in seen:
                seen.add(ip)
                addresses.append(ip)
    except (OSError, socket.gaierror):
        pass

    # 2. UDP-socket trick: "connect" to a public address without sending
    #    anything. The kernel routes through the default NIC and exposes
    #    the chosen source IP via getsockname(). No traffic leaves the box.
    if not addresses:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("8.8.8.8", 80))
                ip = probe.getsockname()[0]
                if _accept(ip) and ip not in seen:
                    addresses.append(ip)
            finally:
                probe.close()
        except OSError:
            pass

    return addresses


def collect_app_pairing_info() -> dict[str, Any]:
    """Build the `--app-info` payload (LAN IPv4, URL, PLOBI_LAN, paired devices).

    Pure read-only helper so tests and other tools can call it without
    going through argparse.
    """
    home = plobi_home()
    plobi_lan = _resolve_plobi_lan()
    lan_on = plobi_lan == "1"
    info = {
        "plobi_home": str(home),
        "plobi_lan_env": plobi_lan,
        "plobi_lan_on": lan_on,
        "bind_host": LAN_BIND_HOST_DEFAULT if lan_on else "127.0.0.1",
        "bind_port": LAN_BIND_PORT_DEFAULT if lan_on else 0,
        "lan_ipv4_addresses": _lan_ipv4_addresses(),
        "pairing_store_path": str(home / "plobi"),
        "urls": [
            f"http://{ip}:{LAN_BIND_PORT_DEFAULT}" for ip in _lan_ipv4_addresses()
        ] if lan_on else [],
    }
    info.update(_pairing_state(home))
    return info


def format_app_pairing_text(info: dict[str, Any]) -> str:
    """Human-readable multi-line summary used by `doctor --app-info`."""
    writable = "writable" if info["pairing_store_writable"] else (
        "NOT WRITABLE — no handset can pair, so a LAN bind is downgraded to loopback"
    )
    lines = [
        f"PLOBI_HOME      = {info['plobi_home']}",
        f"PLOBI_LAN       = {info['plobi_lan_env'] or '(unset)'} "
        f"({'on — bind 0.0.0.0:8787' if info['plobi_lan_on'] else 'off — bind 127.0.0.1:0 (loopback only)'})",
        f"Bind             = {info['bind_host']}:{info['bind_port']}",
        f"Paired devices   = {info['paired_devices']} "
        f"({info['pairing_store_path']}: {writable})",
    ]
    if info.get("pairing_error"):
        lines.append(f"Pairing store    = unreadable: {info['pairing_error']}")
    addresses = info.get("lan_ipv4_addresses") or []
    if addresses:
        lines.append("LAN IPv4         =")
        for ip in addresses:
            lines.append(f"  - {ip}")
        if info["plobi_lan_on"]:
            lines.append("Handset URL(s)   =")
            for url in info.get("urls") or []:
                lines.append(f"  - {url}")
    else:
        lines.append("LAN IPv4         = (none detected — check NIC / VPN / WSL)")
    lines.append(
        "How to pair      = desktop PAIRING page -> 'Pair a device' -> type the 6-digit "
        "code on the handset (5 min, one token per device, revoke one without locking out "
        "the rest). There is no shared App token any more."
    )
    return "\n".join(lines)


def load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Never logs values."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip().strip("'").strip('"')
        if key:
            out[key] = value
    return out


def apply_env_files(home: Path | None = None) -> list[str]:
    """Fill missing os.environ from PLOBI_HOME/.env then repo .env."""
    loaded: list[str] = []
    for path in ( (home or plobi_home()) / ".env", REPO_ROOT / ".env"):
        parsed = load_env_file(path)
        if not parsed:
            continue
        loaded.append(str(path))
        for key, value in parsed.items():
            if key not in os.environ or not str(os.environ.get(key) or "").strip():
                os.environ[key] = value
    return loaded


def parse_aigw_port(config_text: str) -> int:
    """Read ``server.port`` from aigw YAML without requiring PyYAML."""
    in_server = False
    for raw in config_text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"^server:\s*$", line):
            in_server = True
            continue
        if in_server and re.match(r"^\S", line):
            in_server = False
        if in_server:
            match = re.match(r"^\s+port:\s*(\d+)\s*$", line)
            if match:
                return int(match.group(1))
    return DEFAULT_AIGW_PORT


def aigw_base_from_env_or_config() -> str:
    for key in ("PLOBI_AIGW_URL", "PLOBI_QUOTA_AIGW_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    for path in AIGW_CONFIG_CANDIDATES:
        if path.is_file():
            port = parse_aigw_port(path.read_text(encoding="utf-8"))
            return f"http://127.0.0.1:{port}/v1"
    return f"http://127.0.0.1:{DEFAULT_AIGW_PORT}/v1"


def aigw_api_key() -> str:
    return (
        (os.environ.get("AIGW_API_KEY") or os.environ.get("AIGW_KEY") or "sk-local-dev-key").strip()
    )


def http_get_json(url: str, timeout: float = 2.5, headers: dict[str, str] | None = None) -> tuple[int | None, Any]:
    merged = {"Accept": "application/json"}
    if headers:
        merged.update(headers)
    request = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, body[:200]


def check_chatlog(base_url: str = DEFAULT_CHATLOG_URL) -> dict[str, Any]:
    base = base_url.rstrip("/")
    last_code = None
    for path in CHATLOG_HEALTH_PATHS:
        code, _ = http_get_json(f"{base}{path}")
        last_code = code
        if code == 200:
            return {
                "id": "chatlog",
                "color": GREEN,
                "detail": f"{base}{path} ok",
            }
    if last_code is None:
        # R-047: nothing is listening because the collection stack is postponed,
        # not because anything broke. A *reachable* service answering non-200
        # still reports red below — that one is a real fault.
        return deferred_row("chatlog", f"{base} unreachable (no listener)")
    return {
        "id": "chatlog",
        "color": RED,
        "detail": f"{base} HTTP {last_code}",
    }


def check_aigw(base_url: str | None = None) -> dict[str, Any]:
    base = (base_url or aigw_base_from_env_or_config()).rstrip("/")
    models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    code, payload = http_get_json(
        models_url,
        headers={"Authorization": f"Bearer {aigw_api_key()}"},
    )
    if code is None:
        # R-047: no listener at all. Deferral covers "not started yet"; an
        # answered-but-wrong request below is still a real fault.
        return deferred_row("aigw", f"{models_url} unreachable (no listener)", url=models_url)
    if code != 200 or payload is None:
        return {
            "id": "aigw",
            "color": RED,
            "detail": f"{models_url} HTTP {code}",
            "url": models_url,
        }
    names: list[str] = []
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("models") or []
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and row.get("id"):
                    names.append(str(row["id"]))
                elif isinstance(row, str):
                    names.append(row)
    has_workbuddy = any(name.startswith("workbuddy/") for name in names)
    color = GREEN if has_workbuddy else YELLOW
    detail = f"{models_url} {len(names)} models"
    if has_workbuddy:
        detail += "; workbuddy/* present"
    elif names:
        detail += "; no workbuddy/* yet (mock ok for wiring)"
    else:
        detail += "; empty catalog"
    return {
        "id": "aigw",
        "color": color,
        "detail": detail,
        "url": models_url,
        "models": names[:20],
    }


def check_dingtalk() -> dict[str, Any]:
    url = (os.environ.get("DINGTALK_WEBHOOK_URL") or "").strip()
    secret = (os.environ.get("DINGTALK_WEBHOOK_SECRET") or "").strip()
    if not url:
        return {
            "id": "dingtalk",
            "color": RED,
            "detail": "DINGTALK_WEBHOOK_URL missing — add to PLOBI_HOME/.env or Code/.env (never commit)",
        }
    if not secret:
        return {
            "id": "dingtalk",
            "color": YELLOW,
            "detail": "webhook set, DINGTALK_WEBHOOK_SECRET empty (加签 robots will reject)",
        }
    return {
        "id": "dingtalk",
        "color": GREEN,
        "detail": "DINGTALK_WEBHOOK_URL + SECRET present (not sent)",
    }


def soul_has_routing_block(soul: str) -> bool:
    """True if SOUL has L1 routing fence (old HTML or :::PLOBI_L1_ASK_ROUTING:::)."""
    return "PLOBI_L1" in (soul or "")


def _toolsets_from_config(cfg: dict) -> set[str]:
    found: set[str] = set()
    top = cfg.get("toolsets") or []
    if isinstance(top, list):
        found.update(str(item) for item in top)
    platforms = cfg.get("platform_toolsets") or {}
    if isinstance(platforms, dict):
        for key in ("cli", "gateway"):
            listed = platforms.get(key) or []
            if isinstance(listed, list):
                found.update(str(item) for item in listed)
    plugins = cfg.get("plugins") or {}
    if isinstance(plugins, dict):
        enabled = plugins.get("enabled") or []
        if isinstance(enabled, list) and "plobi-north-star" in enabled:
            found.add(NORTH_STAR)
    return found


def check_north_star(home: Path | None = None) -> dict[str, Any]:
    root = home or plobi_home()
    cfg_path = root / "config.yaml"
    if not cfg_path.is_file():
        return {
            "id": "north_star",
            "color": RED,
            "detail": f"no {cfg_path}",
        }
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {
            "id": "north_star",
            "color": RED,
            "detail": f"cannot parse {cfg_path}: {exc}",
        }
    if not isinstance(cfg, dict):
        cfg = {}
    names = _toolsets_from_config(cfg)
    soul = (root / "SOUL.md").read_text(encoding="utf-8") if (root / "SOUL.md").is_file() else ""
    has_block = soul_has_routing_block(soul)
    if NORTH_STAR in names and has_block:
        color, extra = GREEN, "toolset + SOUL routing block"
    elif NORTH_STAR in names:
        color, extra = YELLOW, "toolset on, SOUL routing block missing"
    else:
        color, extra = RED, "plobi_north_star not in toolsets/cli/gateway"
    return {
        "id": "north_star",
        "color": color,
        "detail": f"{cfg_path}: {extra}",
        "toolsets": sorted(names),
    }


def check_chatlog_config(home: Path | None = None) -> dict[str, Any]:
    root = home or plobi_home()
    path = root / "plobi" / "chatlog.json"
    if not path.is_file():
        # R-047: absent means "never set up", which is what deferral looks like.
        # A file that exists but is unreadable / not an object / in the wrong
        # mode means someone did turn the collector on and it is misconfigured,
        # so those branches below stay red.
        return deferred_row("collect", f"{path} missing (first-run blacklist review never done)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"id": "collect", "color": RED, "detail": f"{path} unreadable: {exc}"}
    if not isinstance(data, dict):
        return {"id": "collect", "color": RED, "detail": f"{path} not an object"}
    mode = str(data.get("mode") or "").strip().lower()
    talkers = data.get("talkers") or []
    blacklist = data.get("blacklist") or []
    enabled = data.get("enabled", True)
    collectable = mode == "blacklist" and (isinstance(talkers, list) or True)
    if mode != "blacklist":
        return {
            "id": "collect",
            "color": RED,
            "detail": f"{path} mode={mode or 'unset'} (want blacklist)",
        }
    if enabled is False:
        return {
            "id": "collect",
            "color": YELLOW,
            "detail": f"{path} blacklist but enabled=false",
        }
    extra = f"talkers={len(talkers) if isinstance(talkers, list) else 0} excluded={len(blacklist) if isinstance(blacklist, list) else 0}"
    return {
        "id": "collect",
        "color": GREEN if collectable else YELLOW,
        "detail": f"{path} blacklist {extra}",
    }


def _whitelist_proposal_path(home: Path | None = None) -> Path:
    """Where ``whitelist_report.py`` writes the proposal JSON.

    Honours ``PLOBI_CHATLOG_CONFIG`` (per-file override) the same way the
    collector's :func:`config_path` does, so this never points at the wrong
    ``PLOBI_HOME`` when the operator has pinned chatlog.json elsewhere.
    """
    override = (os.environ.get("PLOBI_CHATLOG_CONFIG") or "").strip()
    if override:
        return Path(override).parent / WHITELIST_PROPOSAL_FILENAME
    return (home or plobi_home()) / "plobi" / WHITELIST_PROPOSAL_FILENAME


def _read_proposal_age_hours(path: Path) -> float | None:
    """Return age in hours of the proposal file, or None if unparseable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamp = str(data.get("generated_at") or "").strip()
        if not stamp:
            return None
        # Proposal timestamps are ISO-8601 in UTC ("...+00:00" or trailing "Z").
        cleaned = stamp.replace("Z", "+00:00")
        generated = datetime.fromisoformat(cleaned)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        return (datetime.now(tz=timezone.utc) - generated).total_seconds() / 3600.0
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def check_chatlog_whitelist_efficiency(home: Path | None = None) -> dict[str, Any]:
    """Reads ``whitelist_proposal.json`` (last sweep) to flag a leaky whitelist.

    The ratio we surface is the share of *active* chatlog talkers that the
    current whitelist (or known set, in blacklist mode) does not capture.
    Anything below :data:`WHITELIST_EFFICIENCY_YELLOW` means most of the
    recent chat traffic is invisible to the collector and the morning brief
    will keep reporting "honest empty".
    """
    proposal_path_value = _whitelist_proposal_path(home)
    if not proposal_path_value.is_file():
        # R-047: there is no sweep because there is no collector running, and the
        # collector is postponed. Once chatlog serves and a sweep has run, the
        # ratio branches below take over and report green/yellow normally.
        return deferred_row(
            "whitelist_eff",
            f"no {proposal_path_value} (no sweep has ever run)",
        )
    try:
        data = json.loads(proposal_path_value.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "id": "whitelist_eff",
            "color": RED,
            "detail": f"{proposal_path_value} unreadable: {exc}",
        }
    if not isinstance(data, dict):
        return {
            "id": "whitelist_eff",
            "color": RED,
            "detail": f"{proposal_path_value} malformed JSON",
        }
    topn = data.get("topn") or []
    if not isinstance(topn, list) or not topn:
        return {
            "id": "whitelist_eff",
            "color": YELLOW,
            "detail": f"{proposal_path_value} has no topn data — re-run whitelist_report.py",
        }

    # The right "is the collector capturing this?" signal depends on the mode
    # the proposal was generated under:
    #   * whitelist — ``in_current_whitelist`` is the gate; False == refused.
    #   * blacklist  — ``in_blacklist`` is the gate; True  == refused.
    # Anything else (no flag set) is treated as not-captured so an unknown
    # proposal shape fails loud rather than silently going green.
    proposal_mode = str(data.get("mode") or "").strip().lower()

    def _is_captured(row: dict[str, Any]) -> bool:
        if proposal_mode == "blacklist":
            return not bool(row.get("in_blacklist"))
        if proposal_mode == "whitelist":
            return bool(row.get("in_current_whitelist"))
        return False

    def _is_refused(row: dict[str, Any]) -> bool:
        if proposal_mode == "blacklist":
            return bool(row.get("in_blacklist"))
        if proposal_mode == "whitelist":
            return not bool(row.get("in_current_whitelist"))
        return True

    captured = sum(1 for row in topn if isinstance(row, dict) and _is_captured(row))
    total = len(topn)
    active_total = int(data.get("active_talkers_total") or 0)
    refused_sample = [
        row.get("talker") for row in topn
        if isinstance(row, dict) and _is_refused(row)
    ][:5]

    # If the proposal is older than the freshness window, treat it as stale —
    # the user may have added more talkers since the snapshot.
    age_hours = _read_proposal_age_hours(proposal_path_value)
    if age_hours is not None and age_hours > WHITELIST_PROPOSAL_FRESH_HOURS:
        return {
            "id": "whitelist_eff",
            "color": YELLOW,
            "detail": (
                f"captured={captured}/{total} of top active traffic "
                f"(active_talkers={active_total}); proposal stale ({age_hours:.1f}h old, "
                f"threshold {WHITELIST_PROPOSAL_FRESH_HOURS}h) — re-run whitelist_report.py"
            ),
        }

    if total == 0:
        return {"id": "whitelist_eff", "color": YELLOW, "detail": "no topn rows"}

    capture_ratio = captured / total
    shortlist = ", ".join(str(t) for t in refused_sample if t)
    detail = (
        f"captured={captured}/{total} of top active traffic "
        f"(active_talkers={active_total}, mode={proposal_mode or '?'}, "
        f"window_hours={data.get('window_hours', '?')})"
    )
    if shortlist:
        detail += f"; top refused: {shortlist}"

    if capture_ratio < WHITELIST_EFFICIENCY_YELLOW:
        return {
            "id": "whitelist_eff",
            "color": YELLOW,
            "detail": (
                f"{detail} — gate is dropping (below "
                f"{int(WHITELIST_EFFICIENCY_YELLOW * 100)}% threshold); "
                "review config (mode/whitelist/blacklist) and re-run whitelist_report.py"
            ),
        }
    return {"id": "whitelist_eff", "color": GREEN, "detail": detail}


def send_dingtalk_test() -> dict[str, Any]:
    try:
        from plobi.notify.dingtalk import DingTalkNotifier
    except Exception as exc:
        return {"id": "dingtalk_send", "color": RED, "detail": f"import failed: {exc}"}
    outcome = DingTalkNotifier().send("Plobi doctor --send-test")
    return {
        "id": "dingtalk_send",
        "color": GREEN if outcome.ok else RED,
        "detail": outcome.detail if hasattr(outcome, "detail") else str(outcome),
    }


def run_checks(*, send_test: bool = False) -> list[dict[str, Any]]:
    apply_env_files()
    rows = [
        check_chatlog(),
        check_aigw(),
        check_dingtalk(),
        check_north_star(),
        check_chatlog_config(),
        check_chatlog_whitelist_efficiency(),
    ]
    if send_test:
        rows.append(send_dingtalk_test())
    return rows


def worst_exit(rows: list[dict[str, Any]]) -> int:
    # Only red fails the run. ``deferred`` is a product decision (R-047), not a
    # fault, so a table of green + deferred must still exit 0.
    if any(row.get("color") == RED for row in rows):
        return 1
    return 0


def format_table(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'id':<14} {'color':<8} detail", "-" * 72]
    for row in rows:
        lines.append(f"{row.get('id', ''):<14} {row.get('color', ''):<8} {row.get('detail', '')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plobi env doctor")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--send-test", action="store_true", help="actually POST a DingTalk test card")
    parser.add_argument(
        "--app-info",
        action="store_true",
        help=(
            "print WP-H1-LAN handset access info (LAN IPv4 / URL / PLOBI_LAN state / "
            "how many devices are paired into this home and whether pairing is "
            "possible). Does NOT post DingTalk and prints no secrets."
        ),
    )
    args = parser.parse_args(argv)

    rows = run_checks(send_test=args.send_test)
    if args.app_info:
        # Apply env files first so a PLOBI_LAN set in PLOBI_HOME/.env
        # is visible to ``_resolve_plobi_lan`` via ``os.environ``.
        apply_env_files()
        info = collect_app_pairing_info()
        print(format_app_pairing_text(info))
        if args.json:
            print(json.dumps({"plobi_home": str(plobi_home()), "app_info": info}, ensure_ascii=False, indent=2))
        return 0

    if args.json:
        print(json.dumps({"plobi_home": str(plobi_home()), "checks": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"PLOBI_HOME={plobi_home()}")
        print(format_table(rows))
        parsed = urlparse(aigw_base_from_env_or_config())
        print(f"aigw probe host={parsed.hostname} port={parsed.port or DEFAULT_AIGW_PORT}")
    return worst_exit(rows)


if __name__ == "__main__":
    sys.exit(main())
