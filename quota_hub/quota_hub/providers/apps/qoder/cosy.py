"""The COSY request signature Qoder's own CLI puts on its model-server calls.

Why this file exists: the user asked why the Qoder leg rides ACP when an HTTP door
obviously exists. It does — `{ChatBase}/algo/api/v2/service/pro/sse/agent_chat_generation`
— but every request on it is signed with a scheme the CLI computes locally, and the
door also expects a machine identity. The scheme is not our invention and not a guess:
it is the one implemented in the MIT-licensed `caigee-cmd/cli2api`
(`internal/providers/qoder/cosy.go`, `qauth/qauth.go`, `qauth/fingerprint.go` — that
package is itself a port of `EchoPing07/Qoder-2API-Go`, MIT). We read it and wrote the
same rules here, in our own language, with attribution:

    Qoder COSY wire shape: Copyright (c) 2026 caigee-cmd (cli2api, MIT)
                          Copyright (c) 2026 EchoPing (Qoder-2API-Go, MIT)

What is *mirrored* (their words, not ours): `Cosy-Version` 1.1.75 tracks the pinned
qodercli build; the UA is the default `undici` leaves on the wire; `Cosy-MachineOS` is
the `${arch}_${platform}` of the machine that runs the client — theirs is a Linux
container, so it is configurable here; the link-local `Cosy-Clientip` is what the
official CLI sends.

What is NOT verified: their own README says the Qoder-CN adapter is implemented but
"真账号验收仍未完成", and every test they ship is a local fake transport. So this module
is pinned to the *rules* we read (md5 of a documented concatenation, a 16-character
temp key that is both the AES key and the IV, base64 of an RSA-PKCS1v15 wrap, the
thirds-rotation + custom alphabet of `Encode=1`, machine fields derived from the uid).
A live call is what proves a detail right; nothing here claims that yet.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

#: The client/protocol version the pinned qodercli sends. One place on purpose:
#: the header, the bearer payload and the chat body must never disagree.
COSY_VERSION = "1.1.75"
COSY_USER_AGENT = "undici"
COSY_MACHINE_OS = "x86_64_linux"
COSY_CLIENT_IP = "169.254.198.161"

#: The server's RSA public key, as embedded in the CLI's client (1024-bit; the wrap of
#: a 16-byte temp key is exactly 128 bytes). It encrypts the session key — it is not a
#: secret and not a credential.
SERVER_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----"""

_STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_CUSTOM = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
_TRANSLATE = str.maketrans(_STD + "=", _CUSTOM + "$")


def _md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def encode_body(plain: bytes) -> str:
    """`Encode=1`'s custom base64: standard b64, rotate by thirds, remap the alphabet.

    The rotation is `last + head + middle` on the *base64 text*, with `n // 3` chars
    moved from the front to the back — it is not a byte-level shuffle, so the result is
    still decodable by undoing the map, the rotation, then standard base64.
    """
    std = base64.b64encode(plain).decode("ascii")
    n = len(std)
    a = n // 3
    rotated = std[n - a:] + std[a:n - a] + std[:a] if a else std
    return rotated.translate(_TRANSLATE)


def decode_body(text: str) -> bytes:
    """The inverse of `encode_body`, for anything the server echoes back encoded."""
    undone = text.translate(str.maketrans(_CUSTOM + "$", _STD + "="))
    n = len(undone)
    a = n // 3
    if not a:
        return base64.b64decode(undone)
    return base64.b64decode(undone[n - a:] + undone[a:n - a] + undone[:a])


def sign_path(raw_url: str) -> str:
    """The path the signature uses: the URL path with the `/algo` prefix stripped."""
    path = urlsplit(raw_url).path or "/"
    if path.startswith("/algo"):
        path = path[len("/algo"):]
    return path or "/"


def _sorted_compact(fields: list[tuple[str, str]]) -> str:
    """Compact JSON with keys in sorted order — the exact bytes both sides must agree on."""
    return json.dumps(dict(fields), separators=(",", ":"), ensure_ascii=False, sort_keys=True)


@dataclass
class Account:
    """The identity fields the CLI keeps for a signed-in user.

    `security_oauth_token` is what the door takes as the session token when it is there;
    `access_token` is the fallback (measured order in their `CLIUser.token()`).
    """

    uid: str = ""
    name: str = ""
    aid: str = ""
    yx_uid: str = ""
    organization_id: str = ""
    organization_name: str = ""
    user_type: str = ""
    access_token: str = ""
    security_oauth_token: str = ""
    refresh_token: str = ""

    def token(self) -> str:
        return (self.security_oauth_token or "").strip() or (self.access_token or "").strip()

    def identity_fields(self) -> list[tuple[str, str]]:
        aid = (self.aid or "").strip() or self.uid
        return [
            ("aid", aid),
            ("name", (self.name or "").strip()),
            ("organization_id", (self.organization_id or "").strip()),
            ("organization_name", (self.organization_name or "").strip()),
            ("refresh_token", (self.refresh_token or "").strip()),
            ("security_oauth_token", self.token()),
            ("uid", self.uid),
            ("user_type", (self.user_type or "").strip()),
            ("yx_uid", (self.yx_uid or "").strip()),
        ]


def machine_id(uid: str) -> str:
    return _md5_hex(f"machine:{uid or 'anonymous'}")


def machine_token(uid: str) -> str:
    digest = hashlib.sha512(f"machinetoken:{uid}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:43]


def machine_type(uid: str) -> str:
    return _md5_hex(f"machinetype:{uid or 'anonymous'}")[:18]


def machine_hostname(uid: str) -> str:
    return "DESKTOP-" + _md5_hex(f"hostname:{uid}").upper()[:7]


class Session:
    """One account's COSY signing state.

    A session mints a 16-character temp key (8 random bytes, hex). That same string is
    the AES-128-CBC key *and* IV for the identity blob, and it is wrapped with the
    server's RSA key to become `Cosy-Key`. Deriving the machine fields from the uid is
    deliberate: a restart must not look like a new device.
    """

    def __init__(self, account: Account, *, temp_key: str | None = None, machine_os: str = COSY_MACHINE_OS) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        if not account.uid or not account.token():
            raise ValueError("COSY session 需要 uid 与会话令牌")
        self.account = account
        self.machine_os = machine_os

        key = (temp_key or secrets.token_bytes(8).hex()).encode("ascii")
        if len(key) != 16:
            raise ValueError("temp key 必须是 16 个字符")
        self._temp_key = key

        public = serialization.load_pem_public_key(SERVER_PUBLIC_KEY_PEM.encode("ascii"))
        wrapped = public.encrypt(key, padding.PKCS1v15())
        self.cosy_key = base64.b64encode(wrapped).decode("ascii")

        identity = _sorted_compact(account.identity_fields()).encode("utf-8")
        padder = len(identity) % 16 or 16
        padded = identity + bytes([padder]) * padder
        encryptor = Cipher(algorithms.AES(self._temp_key), modes.CBC(self._temp_key)).encryptor()
        self.info = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")

        self.machine_id = machine_id(account.uid)
        self.machine_token = machine_token(account.uid)
        self.machine_type = machine_type(account.uid)
        self.machine_hostname = machine_hostname(account.uid)

    def payload(self, request_id: str) -> str:
        body = _sorted_compact([
            ("cosyVersion", COSY_VERSION),
            ("ideVersion", ""),
            ("info", self.info),
            ("requestId", request_id),
            ("version", "v1"),
        ])
        return base64.b64encode(body.encode("utf-8")).decode("ascii")

    def headers(self, *, body: str, raw_url: str, request_id: str, now: float | None = None) -> dict[str, str]:
        """The headers of one signed request.

        The signed string is exactly `payload \\n cosyKey \\n date \\n body \\n path` and
        the bearer is `COSY.<payload>.<md5hex>`; `date` is unix seconds as a string, and
        `body` is the *encoded* body when the URL carries `Encode=1`.
        """
        payload = self.payload(request_id)
        date = str(int(now if now is not None else time.time()))
        raw = "\n".join((payload, self.cosy_key, date, body, sign_path(raw_url)))
        bearer = f"Bearer COSY.{payload}.{_md5_hex(raw)}"
        return {
            "Accept": "text/event-stream",
            "Authorization": bearer,
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "Login-Version": "v2",
            "User-Agent": COSY_USER_AGENT,
            "Cosy-Version": COSY_VERSION,
            "Cosy-Key": self.cosy_key,
            "Cosy-Date": date,
            "Cosy-User": self.account.uid,
            "Cosy-ClientType": "5",
            "Cosy-Data-Policy": "agree",
            "Cosy-MachineId": self.machine_id,
            "Cosy-MachineToken": self.machine_token,
            "Cosy-MachineType": self.machine_type,
            "Cosy-MachineOS": self.machine_os,
            "Cosy-MachineHostname": self.machine_hostname,
            "Cosy-Business-Product": "cli",
            "Cosy-Business-Type": "agent",
            "Cosy-Scene": "assistant",
            "Cosy-Clientip": COSY_CLIENT_IP,
        }
