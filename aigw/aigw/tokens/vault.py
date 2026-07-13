r"""Encrypted credential vault.

Purpose: never keep desktop-app tokens in plaintext on disk. Two key backends:
  - "keyring": master key stored in the OS keyring (macOS Keychain / libsecret /
              Windows Credential Manager). Best when available.
  - "file":    master key written to a 0600 keyfile under the user home.
  - "auto":    keyring if importable + usable, else file.

Blobs are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). Plaintext tokens only
ever live in process memory.

This is *not* a substitute for the upstream auth flow — it only protects tokens at
rest. See README "风险与最佳实践".
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, backend: str = "auto", *, service: str = "aigw",
                 keyfile: Optional[Path] = None, vault_path: Optional[Path] = None):
        """
        backend     : "auto" | "keyring" | "file"
        service     : keyring service name (ignored for file backend)
        keyfile     : where the Fernet key lives (file backend); default ~/.aigw/.key
        vault_path  : where the encrypted blob lives; default ~/.aigw/creds.enc
        """
        self.backend = backend
        self.service = service
        self.keyfile = Path(keyfile) if keyfile else Path.home() / ".aigw" / ".key"
        self.vault_path = Path(vault_path) if vault_path else Path.home() / ".aigw" / "creds.enc"
        self._key = self._load_key()
        self._fernet = Fernet(self._key)
        self._lock = threading.Lock()

    # --- key material ----------------------------------------------------
    def _load_key(self) -> bytes:
        if self.backend in ("keyring", "auto"):
            try:
                import keyring  # type: ignore
                stored = keyring.get_password(self.service, "master-key")
                if stored:
                    return base64.urlsafe_b64decode(stored)
            except Exception:
                if self.backend == "keyring":
                    raise VaultError("keyring backend requested but unavailable")
        # file backend (or auto fallback)
        self.keyfile.parent.mkdir(parents=True, exist_ok=True)
        if self.keyfile.exists():
            return base64.urlsafe_b64decode(self.keyfile.read_text().strip())
        key = Fernet.generate_key()
        self.keyfile.write_text(base64.urlsafe_b64encode(key).decode())
        os.chmod(self.keyfile, 0o600)
        return key

    # --- encrypt / decrypt ----------------------------------------------
    def encrypt_obj(self, obj: Any) -> str:
        return self._fernet.encrypt(json.dumps(obj, ensure_ascii=False).encode()).decode()

    def decrypt_obj(self, token: str) -> Any:
        try:
            return json.loads(self._fernet.decrypt(token.encode()).decode())
        except InvalidToken as e:
            raise VaultError("decrypt failed (wrong key or tampered vault)") from e

    # --- file helpers ----------------------------------------------------
    def save(self, obj: Any) -> None:
        with self._lock:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            self.vault_path.write_text(self.encrypt_obj(obj))
            os.chmod(self.vault_path, 0o600)

    def load(self) -> Optional[Any]:
        if not self.vault_path.exists():
            return None
        with self._lock:
            return self.decrypt_obj(self.vault_path.read_text())

    @property
    def backend_used(self) -> str:
        # report which backend actually produced the key
        try:
            import keyring  # type: ignore
            if keyring.get_password(self.service, "master-key"):
                return "keyring"
        except Exception:
            pass
        return "file"
