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
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
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
#: Plugin *manifest* name — the key ``plugins.enabled`` lists, distinct from the
#: toolset name above (the two differ by ``-`` vs ``_`` and are easy to swap).
NORTH_STAR_PLUGIN = "plobi-north-star"

GREEN, YELLOW, RED = "green", "yellow", "red"

# ---------------------------------------------------------------------------
# DEFERRED — a fourth status: "后置，不是坏了".
#
# It exists because of R-048's finding: a false red destroys the meaning of
# "red == something broke" and drowns the rows that genuinely need fixing. The
# product owner postponed the WeChat collection stack once; **that ruling was
# revoked on 2026-09-30 by 裁定 48**, and the sentence this file used to print
# still cited the revoked number. 裁定 162 keeps the mechanism and fixes the
# reason: deferral may only cover a capability this wave never intended to run,
# and which one that is is read from ONE place — the collectors registry — not
# from a hand-written list that goes stale the day someone flips a switch.
#
# This is NOT the LAN row's trick (see ``collect_app_pairing_info`` above):
# LAN is dropped from the table entirely because it is opt-in read-out. A
# deferred capability stays in the table, still gets probed, and still goes
# GREEN the moment it is really live — deferral is a product decision about
# priority, not a permanent disable. Only a *failed* probe is downgraded, and
# only when the registry says that source is off: a source that IS switched on
# and is not running reports **red** (``deferred_or_red`` below).
#
# ``DEFERRED`` is 8 chars, so it fills the existing ``{'color':<8}`` column
# exactly and the table keeps its alignment.
# ---------------------------------------------------------------------------
DEFERRED = "deferred"

# One line per deferred capability: what it would take to switch it on *on this
# machine*, as measured on the checkout it runs from. Two rules bite here:
#   * never name a launcher that does not exist on the resident machine, and
#   * never state a cause the row did not observe — 裁定 162 第 2 条.
# Each hint says which part is a reading and which part is the remaining step.
DEFERRED_ENABLE_HINTS: dict[str, str] = {
    "chatlog": (
        "把 sidecar 起来监听 :5030（这台上的二进制在 `tools/chatlog/bin/chatlog.exe`；"
        "Go 与 `CHATLOG_DATA_KEY` 齐不齐不是这条探针的读数，所以这里不替它担保）"
    ),
    "aigw": (
        "Plobi 需要时自己起这一条 —— 没有要人手跑的东西"
    ),
    "collect": (
        "做首轮黑名单复核，让 `$PLOBI_HOME/plobi/chatlog.json` 存在且 `mode=blacklist`"
    ),
    "whitelist_eff": (
        "跑一次 `python scripts/plobi/whitelist_report.py`，让 `whitelist_proposal.json` 生成出来"
    ),
}

# The four capabilities that may ever be reported deferred. Kept as a set so
# ``worst_exit`` and any future consumer share one definition of the table.
DEFERRED_CAPABILITY_IDS = frozenset(DEFERRED_ENABLE_HINTS)

#: 每一档背后那条信源在采集注册表里的名字；``None`` = 它不是信源，是应用自己起的服务
#: （所以「这一波打不打算接」不由注册表说，也就永不因它而变红）。
DEFERRED_SOURCE_ID: dict[str, Optional[str]] = {
    "chatlog": "wechat",
    "collect": "wechat",
    "whitelist_eff": "wechat",
    "aigw": None,
}

DEFERRED_SUFFIX = "deferred — 不是坏了：这一档不归「该跑而没跑」那一型，真跑起来自己就变绿。怎么回到绿："


def source_switched_on(check_id: str) -> bool:
    """注册表点亮了这条信源吗 —— 「这一波打算接」的唯一真源在这里，不在这份文案里。

    读不到就按「没打算接」处理：探针说不出为什么时，把一行猜成红是撒谎，
    把它照实报成 deferred 才是读数（裁定 162 第 3 条同一条口径）。
    """
    source = DEFERRED_SOURCE_ID.get(check_id)

    if not source:
        return False

    try:
        from plobi.collectors import registry

        return bool(registry.spec(source).collecting)
    except Exception:  # noqa: BLE001 — 注册表读不到时不许把整张表带崩
        return False


def deferred_or_red(check_id: str, observed: str, **extra: Any) -> dict[str, Any]:
    """该跑而没跑 = 红；这一波根本没打算接 = deferred（裁定 162 第 1 条）。

    ``worst_exit`` 只把红算成失败，所以一条点亮的信源借 deferred 报灰，就等于
    让它躲过自检 —— 那正是 R-048 反过来要防的那一型。
    """
    if source_switched_on(check_id):
        return {
            "id": check_id,
            "color": RED,
            "detail": (
                f"{observed} — 注册表里 `{DEFERRED_SOURCE_ID[check_id]}` 这一档是点亮的"
                "（这一波打算接它），它却没跑起来：这是坏了，不是后置"
            ),
            **extra,
        }

    return deferred_row(check_id, observed, **extra)


def deferred_row(check_id: str, observed: str, **extra: Any) -> dict[str, Any]:
    """Build a DEFERRED row: the real probe result + why it is not a fault.

    ``observed`` is the honest outcome of the probe that was still performed
    (port refused, file absent, HTTP 503, …). It is never elided — a deferred
    row that hides what it found is indistinguishable from a skipped check.
    """
    hint = DEFERRED_ENABLE_HINTS[check_id]
    # "Enable:" promised *steps* in front of a hint that is frequently a
    # statement — the aigw row reads "Plobi 需要时自己起这一条", so an imperative
    # label contradicted the sentence after it. The prefix is an explanation of
    # the path back to green, which is true for both shapes (real steps for
    # chatlog, a statement for the app-owned service). Wording is byte-pinned to
    # apps/desktop/electron/deferred-sidecars.ts — change both together.
    detail = f"{observed} — {DEFERRED_SUFFIX}{hint}"
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
    from plobi.agents.registry import AIGW_URL_ENV_VARS

    for key in AIGW_URL_ENV_VARS:
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    for path in AIGW_CONFIG_CANDIDATES:
        if path.is_file():
            port = parse_aigw_port(path.read_text(encoding="utf-8"))
            return f"http://127.0.0.1:{port}/v1"
    return f"http://127.0.0.1:{DEFAULT_AIGW_PORT}/v1"


# The credential itself is NOT resolved here.  ``plobi.agents.registry`` holds the
# one order (explicit env vars → the gateway's own configured key → the shipped
# development default); this table only reports on it, so a rotated
# ``aigw/config.yaml`` key cannot leave doctor and the runtime disagreeing
# (WP-AIGW-CRED-SOURCE / 裁定 81).
def aigw_api_key() -> str:
    from plobi.agents.registry import aigw_api_key as _shared

    return _shared()


def aigw_credential_source() -> str:
    """Which source the credential above would come from — a label, never a value."""
    from plobi.agents.registry import aigw_credential_source as _shared

    return _shared()


def aigw_key_conflict() -> str:
    """Return a one-line explanation when the key we send is not the gateway's.

    ``""`` means aligned.  Never includes either value — only which two sources
    disagree, because a mismatch is a 401 the operator can fix.
    """
    from plobi.agents.registry import AIGW_GATEWAY_CONFIG_PATH, gateway_declared_api_key

    declared = gateway_declared_api_key()
    sending = aigw_api_key()
    if not declared or not sending or declared == sending:
        return ""
    return (
        f"sending {aigw_credential_source()}, but "
        f"{AIGW_GATEWAY_CONFIG_PATH} declares a different server.api_key"
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


# ---------------------------------------------------------------------------
# WP-QUOTA-KEY-LIVENESS — two rows that did not exist before:
#
#   * ``cred_refs``   : the root ``.env`` still holds an unexpanded 1Password
#     reference (裁定 95 一: hand the reference out AND say so in the table — a
#     ``logger.warning`` is not a surface, the log ring ships inside support bundles).
#   * ``quota_liveness``: per-source credential liveness, probed with a
#     **zero-generation** GET ``/models`` (裁定 88: "the key exists / is long enough /
#     the static check is green" is not evidence of life — ``has_usable_secret()``
#     returns True for a dead key).
#
# Both follow 裁定 95 的判据最终形状: RED only from an error code that came back on a
# real request. Absent from ``/models`` is not a fault on this machine — measured:
# ``glm-4-air`` and ``glm-5v-turbo`` are missing from the listing yet answer 200 with
# real ``tool_calls``, so ``/models`` under-reports. Nothing here spends a token and
# nothing here prints a secret — variable names and fingerprints only.
# ---------------------------------------------------------------------------
OP_REFERENCE_PREFIX = "op://"

#: Upstream's "key is fine, this model id is not served" answer. Matching is on the
#: body text because ``http_get_json`` throws the body away (and its signature is
#: stubbed in tests, so it must not change).
UNKNOWN_MODEL_MARKERS = ("unknown model", "1211")

_COLOR_ORDER = {GREEN: 0, YELLOW: 1, RED: 2}


def _worse(left: str, right: str) -> str:
    """Aggregate row colors: red beats yellow beats green."""
    return left if _COLOR_ORDER[left] >= _COLOR_ORDER[right] else right


def _fingerprint_bytes(data: bytes) -> str:
    """12 hex of sha256 — enough to tell two files apart, never the content."""
    return hashlib.sha256(data).hexdigest()[:12]


def _credential_env_file() -> Path:
    """The root credential ``.env`` — the same source the read path uses (裁定 89 甲)."""
    from plobi_cli.config import credential_env_path

    return Path(credential_env_path())


def _models_probe(
    url: str, api_key: str, timeout: float = 2.5
) -> tuple[int | None, Any, str]:
    """Zero-generation GET that KEEPS the error body, so 400/1211 is readable.

    Returns ``(code, payload, body_text)``; ``code`` is ``None`` when nothing
    answered — which is "not proven", never red.
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # a body we cannot read is still a real code
            body = ""
        return exc.code, None, body
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None, ""
    try:
        return code, json.loads(body), body
    except json.JSONDecodeError:
        return code, None, body[:200]


def _looks_like_unknown_model(body_text: str) -> bool:
    low = (body_text or "").lower()
    return any(marker in low for marker in UNKNOWN_MODEL_MARKERS)


def _model_ids(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    ids: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            value = row.get("id") or row.get("model") or row.get("name")
            if value:
                ids.append(str(value))
        elif row:
            ids.append(str(row))
    return ids


def check_credential_refs() -> dict[str, Any]:
    """Unexpanded ``op://`` references in the root credential ``.env`` (裁定 95 一)."""
    path = _credential_env_file()
    if not path.is_file():
        return {
            "id": "cred_refs",
            "color": GREEN,
            "detail": "root credential .env not present — nothing to expand; keys come from the process environment",
        }

    try:
        data = path.read_bytes()
    except OSError as exc:
        return {
            "id": "cred_refs",
            "color": YELLOW,
            "detail": f"root credential .env unreadable ({type(exc).__name__}) — cannot tell whether a reference was expanded",
        }

    fingerprint = _fingerprint_bytes(data)
    unresolved: list[str] = []
    shadowed: list[str] = []
    for key, value in sorted(load_env_file(path).items()):
        if not str(value).startswith(OP_REFERENCE_PREFIX):
            continue
        # apply_env_files() may have copied the reference itself into os.environ,
        # so "present in the environment" is NOT resolution — only a value that is
        # not itself an op:// reference counts as the expanded one.
        from_env = str(os.environ.get(key) or "").strip()
        if from_env and not from_env.startswith(OP_REFERENCE_PREFIX):
            shadowed.append(key)
        else:
            unresolved.append(key)

    if unresolved:
        return {
            "id": "cred_refs",
            "color": RED,
            "detail": (
                f"{len(unresolved)} 个键在根 .env 里仍是未展开的 {OP_REFERENCE_PREFIX} 引用："
                f"{', '.join(unresolved)}（.env fp={fingerprint}）—— 发出去的就是那串引用本身，"
                f"provider 一定拒。改法：让 1Password 把这些引用展开成实值，或把这几行直接写成实值"
            ),
            "keys": unresolved,
            "env_fingerprint": fingerprint,
        }
    if shadowed:
        return {
            "id": "cred_refs",
            "color": GREEN,
            "detail": f"{len(shadowed)} 个键的 .env 值仍是 {OP_REFERENCE_PREFIX} 引用，但进程环境里有展开后的值（{', '.join(shadowed)}）—— 走的是环境那一份",
            "keys": shadowed,
            "env_fingerprint": fingerprint,
        }
    return {"id": "cred_refs", "color": GREEN, "detail": "根 .env 里没有未展开的引用（查过全部键）", "env_fingerprint": fingerprint}


def check_quota_liveness() -> dict[str, Any]:
    """Per-source credential liveness over a zero-generation GET /models."""
    try:
        from plobi.quota import config as quota_config

        cfg = quota_config.load()
    except Exception as exc:
        return {
            "id": "quota_liveness",
            "color": YELLOW,
            "detail": f"读不到额度池配置，探不了（{type(exc).__name__}）—— 没有真请求回来的错误码，不判红",
        }

    sources = cfg.get("sources") or {}
    if not sources:
        return {"id": "quota_liveness", "color": GREEN, "detail": "额度池里没有源可探"}

    cheap = sorted(
        (name, src) for name, src in sources.items() if str(src.get("kind") or "") != "aigw"
    )
    derived = sorted(
        name for name, src in sources.items() if str(src.get("kind") or "") == "aigw"
    )

    color = GREEN
    parts: list[str] = []
    for name, src in cheap:
        base = str(src.get("base_url") or "").rstrip("/")
        model = str(src.get("model") or "")
        api_key = str(src.get("api_key") or "")
        if not base or not api_key:
            color = _worse(color, YELLOW)
            parts.append(f"{name}: 未验（base_url 或 key 没配齐）")
            continue
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        code, payload, body_text = _models_probe(url, api_key)

        if code is None:
            # Nothing answered proves nothing about the credential — 裁定 95: red only
            # from a code that came back on a real request.
            color = _worse(color, YELLOW)
            parts.append(f"{name}: 未验（{url} 拿不到监听者）")
            continue
        if code in (401, 403):
            color = RED
            parts.append(
                f"{name}: HTTP {code} —— 这条 key 被拒。改法：把新值写进根 .env"
                f"（.env 压过进程环境，shell 里 export 无效），"
                f"且两条路要读同一个变量名（这里读的是 {src.get('key_env') or '该源的 key_env'}）"
            )
            continue
        if code == 400 and _looks_like_unknown_model(body_text):
            color = RED
            parts.append(
                f"{name}: HTTP 400 上游回「模型不认识」—— model={model or '(未设)'} 不被这条 key 服务。"
                f"改法：把该源的 model 换成这条 key 真服务的 id"
            )
            continue
        if code != 200:
            color = _worse(color, YELLOW)
            parts.append(f"{name}: 未验（HTTP {code}，没有可判的错误码）")
            continue

        ids = _model_ids(payload)
        note = f"{name}: 活着（目录 {len(ids)} 条）"
        if model and ids and model.lower() not in {i.lower() for i in ids}:
            # A listing that omits an id is NOT a dead id on this machine — measured:
            # /models under-reports. Reporting this as missing would be a false red.
            note += f"；model={model} 目录未列、服务性未验"
        parts.append(note)

    if derived:
        # These sources exist *because* one /models listed them, and share base_url +
        # key, so a probe here can only re-prove the gateway. Saying so beats a row
        # per app that would look like evidence (裁定 81: no silent skips, no fake green).
        sample = "、".join(derived[:3]) + ("…" if len(derived) > 3 else "")
        parts.append(
            f"aigw 派生 {len(derived)} 源（{sample}）：同 base_url 同 key，探一次只等于重探网关，"
            f"per-app 存活要真流量才能证"
        )

    return {
        "id": "quota_liveness",
        "color": color,
        "detail": "; ".join(parts),
        "sources_probed": len(cheap),
        "sources_derived": len(derived),
    }


def aigw_leg_enabled(config_text: str, leg: str) -> bool | None:
    """Read ``providers.<leg>.enabled`` from aigw YAML without requiring PyYAML.

    ``None`` means the leg is not declared at all. aigw's registry treats an
    *absent* ``enabled`` as true, but an absent **leg** means it is not wired —
    callers must say which of the two they found instead of guessing.
    """
    in_providers = False
    in_leg = False
    for raw in config_text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"^providers:\s*$", line):
            in_providers = True
            continue
        if in_providers and re.match(r"^\S", line):  # next top-level section
            break
        if not in_providers:
            continue
        if re.match(r"^  \S", line):  # a provider entry ends the previous leg
            in_leg = bool(re.match(rf"^  {re.escape(leg)}:\s*$", line))
            continue
        if in_leg:
            match = re.match(r"^\s+enabled:\s*(\S+)\s*$", line)
            if match:
                return match.group(1).strip().lower() in ("true", "yes", "on")
    return None


def _desktop_token_status() -> dict[str, Any]:
    """Indirection so tests can pin this probe without a desktop app installed.

    Never returns a token — see ``antigravity_desktop_token_status``.
    """
    aigw_root = REPO_ROOT / "aigw"
    if str(aigw_root) not in sys.path:
        sys.path.insert(0, str(aigw_root))
    from aigw.tokens import desktop_stores as ds

    return ds.antigravity_desktop_token_status()


def check_subscription_desktop_store() -> dict[str, Any]:
    """Lifetime of the desktop app's subscription token (no network, no spend,
    and it never reads a token value).

    Code `85328dfe` made aigw able to use the access token Antigravity renews and
    writes back on Windows, so "can this leg answer" is settled. The one failure
    left is that **we cannot renew it ourselves** (no OAuth client pair), which is
    why this row reports minutes, not reachability. Yellow rather than red
    throughout: a stale credential is not an error code that came back from a real
    request (WP-QUOTA-KEY-LIVENESS 判据).
    """
    try:
        status = _desktop_token_status()
    except Exception as exc:
        return {
            "id": "subscription_desktop_store",
            "color": YELLOW,
            "detail": f"桌面存储探不动（{type(exc).__name__}）—— 没探到就不判红",
        }

    leg = None
    for path in AIGW_CONFIG_CANDIDATES:
        if path.is_file():
            leg = aigw_leg_enabled(path.read_text(encoding="utf-8"), "antigravity")
            break
    wired = (
        "已挂进网关的 providers（会进选路）"
        if leg is True
        else "默认关着（providers.antigravity.enabled: false）"
        if leg is False
        else "config.yaml 里没有 providers.antigravity 这一条"
    )
    source = status.get("source")
    if not status.get("present"):
        return {
            "id": "subscription_desktop_store",
            "color": YELLOW,
            "detail": f"桌面订阅凭据不在场（source={source}）—— {wired}；"
                      f"要用这条腿，先打开 agy 登一次（它会把凭据写回本机存储）",
        }
    left = status.get("expires_in_seconds")
    if left is None:
        return {
            "id": "subscription_desktop_store",
            "color": GREEN,
            "detail": f"桌面订阅凭据在场（source={source}），剩余寿命未知——那份存储不写过期的戳；{wired}",
        }
    minutes = int(round(abs(left) / 60.0))
    if left <= 0:
        return {
            "id": "subscription_desktop_store",
            "color": YELLOW,
            "detail": f"桌面订阅凭据已过期约 {minutes} 分钟 —— 我们那对 OAuth client 参数取不到，"
                      f"自己续不了；打开 agy 跑一次就会回写新的。{wired}",
        }
    return {
        "id": "subscription_desktop_store",
        "color": GREEN,
        "detail": f"桌面订阅凭据在场，剩余约 {minutes} 分钟（agy 每跑一次就续一期）—— {wired}",
    }


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
        # Nothing is listening. Whether that is a fault or a deferral is not a
        # wording question — the collectors registry owns it: `wechat` switched
        # on means this wave intends to collect, so a silent port is RED.
        # A *reachable* service answering non-200 still reports red below —
        # that one is a real fault either way.
        return deferred_or_red("chatlog", f"{base} unreachable (no listener)")
    return {
        "id": "chatlog",
        "color": RED,
        "detail": f"{base} HTTP {last_code}",
    }


def _aigw_quota_apps(rows: list[Any]) -> tuple[list[str], list[str]]:
    """Split a gateway catalog into (model ids, real quota apps).

    Grouping is by the ``provider`` field, **not** by the ``<app>/`` id prefix.
    This is not a style choice: the gateway's mock channel serves
    ``workbuddy/deepseek-chat`` (see ``aigw/config.yaml``), so prefix matching
    reports a demo channel as a real one and the row goes green with zero actual
    quota. Same rule ``plobi.quota.gateway`` derives by.
    """
    ids: list[str] = []
    apps: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            ids.append(row)
            continue
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "")
        if not model_id:
            continue
        ids.append(model_id)
        provider = str(row.get("provider") or "").strip().lower()
        if provider and provider != "mock":
            apps.add(provider)
    return ids, sorted(apps)


def check_aigw(base_url: str | None = None) -> dict[str, Any]:
    base = (base_url or aigw_base_from_env_or_config()).rstrip("/")
    models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    conflict = aigw_key_conflict()
    code, payload = http_get_json(
        models_url,
        headers={"Authorization": f"Bearer {aigw_api_key()}"},
    )
    if code is None:
        # No listener at all. Deferral covers "not started yet"; an
        # answered-but-wrong request below is still a real fault.
        detail = f"{models_url} unreachable (no listener)"
        if conflict:
            # Unreachable tells us nothing about the credential; the config
            # comparison does, and it would otherwise stay invisible until the
            # gateway is up and answers 401.
            detail = f"{detail}; {conflict}"
        return deferred_row("aigw", detail, url=models_url)
    if code == 401:
        # The one failure mode this row used to describe as a bare number.
        return {
            "id": "aigw",
            "color": RED,
            "detail": (
                f"{models_url} HTTP 401 — the gateway rejected our credential"
                + (f" ({conflict})" if conflict else " (no gateway config to compare against)")
            ),
            "url": models_url,
            "credential": aigw_credential_source(),
        }
    if code != 200 or payload is None:
        return {
            "id": "aigw",
            "color": RED,
            "detail": f"{models_url} HTTP {code}" + (f"; {conflict}" if conflict else ""),
            "url": models_url,
        }
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    ids, apps = _aigw_quota_apps(rows if isinstance(rows, list) else [])
    if conflict:
        # It answered, so *this* gateway takes the key we send — but the config
        # in the tree says otherwise. Not green: the next gateway started from
        # that file will 401, and nothing in the table would have said why.
        color = YELLOW
        verdict = conflict
    elif apps:
        verdict = f"quota app(s): {', '.join(apps)}"
        color = GREEN
    elif ids:
        # Up and answering, but everything it serves is the gateway's own demo
        # channel. Not green (nothing is spendable), not red (nothing is broken).
        verdict = "no quota app wired (catalog is only the gateway demo channel)"
        color = YELLOW
    else:
        verdict = "empty catalog"
        color = YELLOW
    return {
        "id": "aigw",
        "color": color,
        "detail": f"{models_url} {len(ids)} models; {verdict}",
        "url": models_url,
        "models": ids[:20],
        "apps": apps,
        "credential": aigw_credential_source(),
    }


def _dingtalk_referenced(home: Path) -> str:
    """Is anything on this machine actually counting on DingTalk? Returns the reason, or "".

    为什么要有这一步（2026-10-13，用户口径）：钉钉是**上游底座保留的能力**，本机现在不走它
    —— 一条"没配 URL"的 RED 说的却是"坏了"，而真相是"没接线"。红要留给真故障：
    只有当某个 cron 作业的投递口或某个已启用的平台指向钉钉时，缺 URL 才是真断了。
    """
    jobs_path = home / "cron" / "jobs.json"
    if jobs_path.is_file():
        try:
            blob = jobs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            blob = ""
        try:
            parsed = json.loads(blob) if blob else None
        except ValueError:
            parsed = None
        if parsed is None:
            if "dingtalk" in blob.lower():
                # 读不动也要诚实：宁可说"有人指着它"，也别把真断链报成"没接线"。
                return f"{jobs_path.name} 里有 dingtalk 字样（这份作业表没能逐条解析）"
        else:
            jobs = parsed.get("jobs") if isinstance(parsed, dict) else parsed
            for job in jobs if isinstance(jobs, list) else []:
                if not isinstance(job, dict):
                    continue
                name = str(job.get("name") or job.get("id") or "?")
                # ``deliver`` 是真字段（"origin" / "local" / "telegram" / …），
                # ``origin`` 记的是作业创建自哪个平台——"deliver": "origin" 时钉钉就藏在它里面。
                for key in ("deliver", "delivery", "deliver_to", "platform", "platforms", "to", "origin"):
                    value = job.get(key)
                    if value is None or "dingtalk" not in json.dumps(value, ensure_ascii=False).lower():
                        continue
                    return f"cron 作业 {name} 把钉钉当投递口（{key}）"

    cfg_path = home / "config.yaml"
    if cfg_path.is_file():
        try:
            import yaml

            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8", errors="replace")) or {}
        except Exception:
            cfg = {}
        if isinstance(cfg, dict):
            for section in ("gateway", "messaging", "platforms", "deliver"):
                blob = cfg.get(section)
                if blob is None:
                    continue
                try:
                    text = json.dumps(blob, ensure_ascii=False).lower()
                except TypeError:
                    text = str(blob).lower()
                if "dingtalk" in text:
                    return f"config.yaml 的 {section} 段指向钉钉"

    return ""


def check_dingtalk(home: Path | None = None) -> dict[str, Any]:
    url = (os.environ.get("DINGTALK_WEBHOOK_URL") or "").strip()
    secret = (os.environ.get("DINGTALK_WEBHOOK_SECRET") or "").strip()
    if not url:
        referenced = _dingtalk_referenced(home or plobi_home())
        if referenced:
            return {
                "id": "dingtalk",
                "color": RED,
                "detail": f"{referenced}，但 DINGTALK_WEBHOOK_URL 没配 —— 写进 PLOBI_HOME/.env 或 Code/.env（绝不提交）",
            }
        return {
            "id": "dingtalk",
            "color": GREEN,
            "detail": "未接线：没有任何作业或平台指向钉钉（底座保留能力，不是故障；要用再把 URL 写进 .env）",
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
        if isinstance(enabled, list) and NORTH_STAR_PLUGIN in enabled:
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


def _authored_l1_toolsets(cfg: dict) -> set[str]:
    """Toolset names this profile's config actually lists (no defaults merged in).

    Deliberately *not* :func:`_toolsets_from_config`: that one infers
    ``plobi_north_star`` from ``plugins.enabled`` because it asks "would the
    toolset be visible". This asks "is what landed on disk the narrow L1 list",
    and an inference would grade its own homework.
    """
    names: set[str] = set()
    top = cfg.get("toolsets")
    if isinstance(top, list):
        names.update(str(item) for item in top)
    platforms = cfg.get("platform_toolsets") or {}
    if isinstance(platforms, dict):
        for key in ("cli", "gateway"):
            listed = platforms.get(key)
            if isinstance(listed, list):
                names.update(str(item) for item in listed)
    return names


def check_l1_secretary_form(home: Path | None = None) -> dict[str, Any]:
    """Three questions about this machine's L1, answered in one read-only row.

    总秘书形态 is not a profile — it is a handful of keys in the default home's
    ``config.yaml`` plus a fence in its ``SOUL.md``. The backend applies them at
    boot (:func:`plobi.agents.registry.ensure_l1_secretary_form`), so a red row
    here means that pass never ran, was skipped, or something reverted it after.
    """
    from plobi.agents.registry import (
        L1_DROP_TOOLSETS,
        MASTER_TOOLSET_NAME,
        _is_full_plobi_composite,
    )

    root = home or plobi_home()
    cfg_path = root / "config.yaml"
    if not cfg_path.is_file():
        return {
            "id": "l1_form",
            "color": RED,
            "detail": f"no {cfg_path}: plugin=NO; narrow toolsets=NO; SOUL fence=NO "
            "(L1 秘书形态 never applied — start `plobi serve` / `plobi dashboard`)",
        }
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {"id": "l1_form", "color": RED, "detail": f"cannot parse {cfg_path}: {exc}"}
    if not isinstance(cfg, dict):
        cfg = {}

    plugins = cfg.get("plugins") or {}
    enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
    plugin_on = isinstance(enabled, list) and NORTH_STAR_PLUGIN in {str(n) for n in enabled}

    names = _authored_l1_toolsets(cfg)
    workers = sorted(n for n in names if n in L1_DROP_TOOLSETS or _is_full_plobi_composite(n))
    narrow = bool(names) and MASTER_TOOLSET_NAME in names and not workers

    soul = (root / "SOUL.md").read_text(encoding="utf-8") if (root / "SOUL.md").is_file() else ""
    has_fence = soul_has_routing_block(soul)
    answers = (
        ("plugin", plugin_on),
        ("narrow toolsets", narrow),
        ("SOUL fence", has_fence),
    )
    missing = [label for label, ok in answers if not ok]
    detail = f"{cfg_path}: " + "; ".join(
        f"{label}={'yes' if ok else 'NO'}" for label, ok in answers
    )
    if workers:
        detail += f" (worker toolsets still listed: {', '.join(workers)})"
    if missing:
        detail += f" — missing: {', '.join(missing)}"
    return {
        "id": "l1_form",
        "color": GREEN if not missing else RED,
        "detail": detail,
        "plugin_enabled": plugin_on,
        "narrow_toolsets": narrow,
        "soul_fence": has_fence,
    }


def check_chatlog_config(home: Path | None = None) -> dict[str, Any]:
    root = home or plobi_home()
    path = root / "plobi" / "chatlog.json"
    if not path.is_file():
        # Absent means "首轮复核从没做过" —— 那是 deferred 的样子，但同样归注册表判：
        # `wechat` 点亮时，采到了微信却没有这份配置就是坏，不是后置。
        # A file that exists but is unreadable / not an object / in the wrong
        # mode means someone did turn the collector on and it is misconfigured,
        # so those branches below stay red.
        return deferred_or_red("collect", f"{path} missing (first-run blacklist review never done)")
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
        # 这份产物一次都没生成过。以前它总是跟着「采集整栈后置」一起报灰，而那
        # 个理由 2026-09-30 就撤了：`wechat` 在注册表里点着的今天，没有 proposal
        # 就是复核环节没跑，属红；只有这一波没打算接时才是 deferred。
        return deferred_or_red(
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


def check_mind_sync(home: Path | None = None) -> dict[str, Any]:
    """One line on the surface: did the brain get pushed/pulled, when, and how did it end.

    Why this row exists: 定时推拉 whose result nobody can see is not a backup, it is a
    hope. Colour discipline matters — a missing trail is YELLOW (the task may be freshly
    registered, or this machine has no route to GitHub right now, which is not a fault),
    while a last run that did not exit 0 is RED, because that is the case where the
    irreplaceable copy is quietly not leaving the box.
    """
    root = home or plobi_home()
    log = root / "logs" / "mind-sync.log"
    if not log.is_file():
        return {
            "id": "mind_sync",
            "color": YELLOW,
            "detail": f"没有同步留痕（期望 {log}）；任务侧读法见 runbooks/environment.md",
        }
    try:
        lines = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError as exc:
        return {"id": "mind_sync", "color": RED, "detail": f"读不了 {log}: {exc}"}

    stamp, exit_code, summary = "", None, ""
    for line in reversed(lines):
        head, sep, body = line.partition("] ")
        if not sep:
            continue
        body = body.strip()
        if exit_code is None and body.startswith("exit="):
            stamp = head.lstrip("[").strip()
            try:
                exit_code = int(body.split()[0].split("=", 1)[1])
            except ValueError:
                exit_code = None
        if not summary and (body.startswith(("done:", "FAIL", "REFUSE", "SKIP"))):
            summary = body
        if exit_code is not None and summary:
            break
    if exit_code is None:
        return {"id": "mind_sync", "color": YELLOW,
                "detail": f"{log.name} 里还没有带退出码的收尾（最后一行：{lines[-1][:60]}）"}
    label = {20: "分叉等人判", 21: "MIND_ROOT 不是脑仓自己", 22: "分支无 upstream",
             23: "工作树脏，两条腿都没动", 30: "网络/git 失败"}.get(exit_code, f"退出码 {exit_code}")
    detail = f"上次 {stamp or '?'} exit={exit_code}"
    if exit_code:
        detail += f"（{label}）"
    if summary:
        detail += f" · {summary[:96]}"
    return {"id": "mind_sync", "color": GREEN if exit_code == 0 else RED, "detail": detail}


def run_checks(*, send_test: bool = False) -> list[dict[str, Any]]:
    apply_env_files()
    rows = [
        check_chatlog(),
        check_aigw(),
        check_credential_refs(),
        check_quota_liveness(),
        check_subscription_desktop_store(),
        check_dingtalk(),
        check_north_star(),
        check_l1_secretary_form(),
        check_chatlog_config(),
        check_chatlog_whitelist_efficiency(),
        check_mind_sync(),
    ]
    if send_test:
        rows.append(send_dingtalk_test())
    return rows


def worst_exit(rows: list[dict[str, Any]]) -> int:
    # Only red fails the run. ``deferred`` is a priority decision the registry
    # owns (裁定 162), not a fault, so a table of green + deferred exits 0 —
    # while a source the registry switched on and did not come up arrives here
    # as red, which is precisely what stops it hiding in this exit code.
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
