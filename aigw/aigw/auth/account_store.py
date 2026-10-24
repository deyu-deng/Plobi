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
from pathlib import Path
from typing import Any

ACCOUNTS_ENV = "AIGW_ANTIGRAVITY_ACCOUNTS"
CLIENT_ENV = "AIGW_ANTIGRAVITY_OAUTH_CLIENT"
CLIENT_ID_ENV = "GOOGLE_CC_CLIENT_ID"
CLIENT_SECRET_ENV = "GOOGLE_CC_CLIENT_SECRET"


def _root() -> Path:
    home = os.environ.get("PLOBI_HOME", "").strip()
    if home:
        return Path(home) / "plobi"
    return Path.home() / ".plobi" / "plobi"


def accounts_path() -> Path:
    override = os.environ.get(ACCOUNTS_ENV, "").strip()
    return Path(override) if override else _root() / "antigravity_accounts.json"


def client_path() -> Path:
    override = os.environ.get(CLIENT_ENV, "").strip()
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


def client_credentials() -> tuple[str, str]:
    """Environment wins; otherwise the pair saved during bootstrap; else empty."""
    env_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    env_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    if env_id and env_secret:
        return env_id, env_secret
    data = _read_json(client_path(), {})
    if isinstance(data, dict):
        return (
            str(data.get("client_id") or "").strip(),
            str(data.get("client_secret") or "").strip(),
        )
    return "", ""
