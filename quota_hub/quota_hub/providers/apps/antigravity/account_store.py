"""Owner-only storage for the Antigravity OAuth bootstrap.

Two files, both outside git and both written with mode 0600 where the OS allows:

* ``antigravity_accounts.json`` — one record per signed-in Google account
  (refresh token, email, project id). This is what the provider discovers.
* ``antigravity_oauth_client.json`` — the OAuth client pair used to refresh those
  tokens. It is written only when the user supplies the pair through
  ``GOOGLE_CC_CLIENT_ID`` / ``GOOGLE_CC_CLIENT_SECRET`` at bootstrap time, so a
  later gateway restart can refresh without asking for the pair again.

No token value is ever printed or placed in the repository. ``run_bootstrap``
prints the destination path and the account e-mail only.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ACCOUNTS_ENV = "QUOTA_HUB_ANTIGRAVITY_ACCOUNTS"
LEGACY_ACCOUNTS_ENV = "AIGW_ANTIGRAVITY_ACCOUNTS"
CLIENT_ENV = "QUOTA_HUB_ANTIGRAVITY_OAUTH_CLIENT"
LEGACY_CLIENT_ENV = "AIGW_ANTIGRAVITY_OAUTH_CLIENT"
CLIENT_ID_ENV = "GOOGLE_CC_CLIENT_ID"
CLIENT_SECRET_ENV = "GOOGLE_CC_CLIENT_SECRET"
APP_SCAN_ENV = "QUOTA_HUB_ANTIGRAVITY_APP_SCAN"
LEGACY_APP_SCAN_ENV = "AIGW_ANTIGRAVITY_APP_SCAN"

_CLIENT_ID_RE = re.compile(rb"[0-9]{6,}-[a-z0-9]{10,}\.apps\.googleusercontent\.com")
#: Google's public client secrets are `GOCSPX-` plus exactly 28 characters (35 total).
#: Measured on this machine 2026-10-31: `antigravity-tools.exe` holds that constant with
#: another alphanumeric string packed immediately after it and no separator until a NUL 60
#: bytes in, so an open-ended `{10,}` captured both and stored 60 characters - Google then
#: answered `invalid_client`, and we misread that as "the app ships its own runtime client".
#: A bounded `{28}` takes the constant and stops; a negative lookahead would be worse here,
#: because the next byte is a legal character, so nothing would match at all.
_CLIENT_SECRET_RE = re.compile(rb"GOCSPX-[A-Za-z0-9_-]{28}")
_CLIENT_SECRET_SHAPE = re.compile(r"^GOCSPX-[A-Za-z0-9_-]{28}$")


def _is_an_over_captured_public_secret(secret: str) -> bool:
    """Only Google's own public-constant shape can be judged wrong.

    A saved pair that does not start with `GOCSPX-` is the operator's own OAuth client and
    is taken as given - we have no business refusing it.
    """
    return secret.startswith("GOCSPX-") and not _CLIENT_SECRET_SHAPE.match(secret)


def _root() -> Path:
    home = os.environ.get("PLOBI_HOME", "").strip()
    if home:
        return Path(home) / "plobi"
    return Path.home() / ".plobi" / "plobi"


def accounts_path() -> Path:
    override = (
        os.environ.get(ACCOUNTS_ENV, "").strip()
        or os.environ.get(LEGACY_ACCOUNTS_ENV, "").strip()
    )
    return Path(override) if override else _root() / "antigravity_accounts.json"


def client_path() -> Path:
    override = (
        os.environ.get(CLIENT_ENV, "").strip()
        or os.environ.get(LEGACY_CLIENT_ENV, "").strip()
    )
    return Path(override) if override else _root() / "antigravity_oauth_client.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_private(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows ACLs are inherited; mode bits are best effort only
    tmp.replace(path)


def load_accounts() -> list[dict[str, Any]]:
    data = _read_json(accounts_path(), {"version": 1, "accounts": []})
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return [row for row in (accounts or []) if isinstance(row, dict)]


def upsert_account(account: dict[str, Any]) -> dict[str, Any]:
    """Insert/replace by e-mail, so re-running auth refreshes the same row."""
    email = str(account.get("email") or "").strip().lower()
    account_id = str(account.get("id") or "").strip()
    rows = load_accounts()
    fresh: list[dict[str, Any]] = []
    replaced = False
    for row in rows:
        same = (
            (email and str(row.get("email") or "").strip().lower() == email)
            or (account_id and str(row.get("id") or "") == account_id)
        )
        if same and not replaced:
            fresh.append(account)
            replaced = True
        elif not same:
            fresh.append(row)
    if not replaced:
        fresh.append(account)
    _write_private(accounts_path(), {"version": 1, "accounts": fresh})
    return account


def save_client(client_id: str, client_secret: str) -> None:
    if not client_id or not client_secret:
        return
    _write_private(
        client_path(),
        {"client_id": client_id, "client_secret": client_secret, "version": 1},
    )


def _candidate_binaries() -> list[Path]:
    """Apps that ship the Antigravity OAuth client pair (public client, not a user token)."""
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Antigravity",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity",
        Path(r"D:\Software\Antigravity Tools"),
        Path(r"D:\Software\Antigravity"),
    ]
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            out.extend(p for p in root.rglob("*.exe") if p.is_file())
        except OSError:
            continue
    return out


def _find_pair_in_file(path: Path, *, chunk: int = 8 * 1024 * 1024, window: int = 512) -> tuple[str, str]:
    """Scan a binary in chunks for a client id sitting next to its own secret.

    Two facts measured on this machine 2026-10-31:

    * the IDE executable is 234 MB, so skipping files over 200 MB meant discovery found
      nothing at all and the stale value stayed in place for ever;
    * `antigravity-tools.exe` embeds **several** OAuth clients. Taking the first id and the
      first secret independently paired one product's id with another product's secret,
      which Google answers `invalid_client` - the exact thing we blamed on "the app ships
      its own runtime client" on 2026-10-28.

    So a hit only counts when the secret sits within `window` bytes of its own id.
    """
    try:
        with path.open("rb") as handle:
            tail = b""
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                data = tail + block
                for match in _CLIENT_SECRET_RE.finditer(data):
                    start = max(0, match.start() - window)
                    near = _CLIENT_ID_RE.search(data[start:match.start()])
                    if near:
                        return near.group(0).decode("ascii"), match.group(0).decode("ascii")
                tail = data[-window:]
    except OSError:
        return "", ""
    return "", ""


def discover_client_from_apps() -> tuple[str, str]:
    """Read the pair from an installed Antigravity app.

    This is not a user credential: it is the public OAuth client that the
    Antigravity IDE / Antigravity-Manager already ship. We copy it into the
    owner-only file so the user never has to type or paste it, and it never
    enters the repository. Set ``QUOTA_HUB_ANTIGRAVITY_APP_SCAN=0`` to disable.
    """
    scan = os.environ.get(APP_SCAN_ENV) or os.environ.get(LEGACY_APP_SCAN_ENV) or ""
    if scan.strip().lower() in {"0", "false", "no"}:
        return "", ""
    for path in _candidate_binaries():
        try:
            if not path.is_file() or path.stat().st_size > 1024 * 1024 * 1024:
                continue
            pair = _find_pair_in_file(path)
        except OSError:
            continue
        if pair[0] and pair[1]:
            return pair
    return "", ""


def client_credentials(*, persist_discovered: bool = True) -> tuple[str, str]:
    """Env > saved owner-only file > public pair shipped by an installed app."""
    env_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    env_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    if env_id and env_secret:
        return env_id, env_secret
    data = _read_json(client_path(), {})
    if isinstance(data, dict):
        saved_id = str(data.get("client_id") or "").strip()
        saved_secret = str(data.get("client_secret") or "").strip()
        if saved_id and saved_secret:
            # A secret saved by the old open-ended extractor is Google's 35-character
            # constant plus whatever ran into it, and Google refuses it; drop that and let
            # the re-discovery write the clean one. Anything else the operator stored is
            # their own client and is taken as given.
            if not _is_an_over_captured_public_secret(saved_secret):
                return saved_id, saved_secret
    found = discover_client_from_apps()
    if found[0] and found[1]:
        if persist_discovered:
            save_client(*found)
        return found
    return "", ""
