"""Read a subscription out of a **local client's own cache** (WP-EGRESS-ROUTING).

Some providers ship a desktop client and no "copy subscription link" button.  The
link is still on this machine: the client fetched a Clash-shaped configuration
and cached it, encrypted with DPAPI (per-user, per-machine).  Reading that cache
is the difference between "this provider is unusable" and "one command".

Three things this module is careful about:

* **It reads only what the user already has on this box.**  The store is the
  user's own file, decrypted with the user's own DPAPI context; nothing is
  fetched, no account is touched, no credential is printed.
* **It says where the bytes came from.**  The caller gets the path and the key
  name, so a surprising configuration can be traced instead of guessed at.
* **It never returns a credential value in a message.**  The body is handed to
  :mod:`agent.egress.subscription`, which moves every secret into the root
  ``.env`` and leaves only ``*_env`` names in the table.

Only Windows has DPAPI, so this is a Windows-only path.  Everywhere else the
caller gets one sentence saying so, not a traceback.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
from typing import Any

#: One known client: where it keeps the encrypted store, and which key inside it
#: holds the subscription body.
KNOWN_CLIENTS: dict[str, dict[str, str]] = {
    "ikuuu": {
        "store": r"%APPDATA%\org.ikuuu\ikuuuvpn\flutter_secure_storage.dat",
        "key": "riverpod_persist:ikuuu_subscription",
        "note": "iKuuuVPN（Flutter 客户端）",
    },
}


class LocalClientError(Exception):
    """A refusal the user can act on: the reason is in the message, never a traceback."""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.c_void_p)]


def _unprotect(raw: bytes) -> bytes:
    """Decrypt a DPAPI blob in the current user's context."""
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    buf = ctypes.create_string_buffer(raw, len(raw) or 1)
    src = _DATA_BLOB(len(raw), ctypes.addressof(buf))
    out = _DATA_BLOB()
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    if not crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed (gle=%d)" % ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _unwrap_bag(text: str) -> str:
    """Peel the JSON layers a client wraps its store in, and return the body."""
    for _ in range(4):
        try:
            decoded = json.loads(text)
        except Exception:  # noqa: BLE001 — a plain string is a valid end point
            break
        if isinstance(decoded, dict) and "value" in decoded:
            text = str(decoded["value"])
            continue
        if isinstance(decoded, str):
            text = decoded
            continue
        text = json.dumps(decoded)
    try:
        record = json.loads(text)
    except Exception:  # noqa: BLE001
        return text
    if isinstance(record, dict):
        for key in ("content", "subscription", "body"):
            if record.get(key):
                return str(record[key])
    return text


def read_cache(client: str = "ikuuu") -> tuple[str, str]:
    """The subscription body a local client cached, plus where it came from.

    Returns ``(body, provenance)`` — the provenance names the file and the key,
    so the reading can be re-run by hand.
    """
    spec = KNOWN_CLIENTS.get(client)
    if spec is None:
        known = ", ".join(sorted(KNOWN_CLIENTS)) or "(none)"
        raise LocalClientError(f"不认识的客户端 {client!r}；认得的是：{known}")
    if os.name != "nt":
        raise LocalClientError("本机客户端的缓存是 DPAPI 加密的，只有 Windows 读得了")
    path = os.path.expandvars(spec["store"])
    if not os.path.isfile(path):
        raise LocalClientError(
            f"{spec['note']} 的缓存不在 {path}——它可能还没登录过，或缓存换了地方"
        )
    try:
        bag = json.loads(_unprotect(open(path, "rb").read()).decode("utf-8", "replace"))
    except OSError as exc:
        raise LocalClientError(f"读不出 {path}：{type(exc).__name__}: {exc}") from None
    except Exception as exc:  # noqa: BLE001
        raise LocalClientError(
            f"解密 {path} 失败：{type(exc).__name__}: {exc}——这份缓存不是当前用户写的"
        ) from None
    if not isinstance(bag, dict):
        raise LocalClientError(f"{path} 解出来不是一个键值表，没法找订阅")
    key = spec["key"]
    if key not in bag:
        raise LocalClientError(
            f"{path} 里没有 {key}——{spec['note']} 还没把订阅拉下来（在它的界面里更新一次再来）"
        )
    body = _unwrap_bag(str(bag[key]))
    match = re.search(r"^(proxy-groups:|mixed-port:|proxies:)", body, re.M)
    if match:
        body = body[match.start():]
    if not body.strip():
        raise LocalClientError(f"{path} 的 {key} 是空的")
    return body, f"{path} :: {key}"


def clients() -> list[str]:
    """The clients this module can read, by name."""
    return sorted(KNOWN_CLIENTS)


def describe() -> list[str]:
    """One line per known client — where its cache is and what is inside it."""
    lines: list[str] = []
    for name in sorted(KNOWN_CLIENTS):
        spec = KNOWN_CLIENTS[name]
        path = os.path.expandvars(spec["store"])
        state = "在" if os.path.isfile(path) else "不在"
        lines.append(f"{name}: {spec['note']} — 缓存{state}（{path}）")
    return lines


def client_names() -> list[dict[str, Any]]:
    """The same list in a machine-readable shape (for the desktop pane)."""
    return [
        {"id": name, "note": spec["note"], "key": spec["key"]}
        for name, spec in sorted(KNOWN_CLIENTS.items())
    ]
