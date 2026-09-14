"""Authenticated encryption for values persisted by the web application."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet


_KEY_LOCK = threading.Lock()


class SecretBox:
    """Encrypt and decrypt strings with a persistent, local Fernet key.

    The key belongs to the configured data directory and is generated only
    once.  Secret values are represented as URL-safe Fernet token strings in
    SQLite; the plaintext is never written by this class.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.key_path = self.data_dir / "secret.key"
        with _KEY_LOCK:
            self._ensure_key()
        self._fernet = Fernet(self.key_path.read_bytes())

    def _ensure_key(self) -> None:
        """Create the key atomically when this data directory is new."""

        try:
            key_fd = os.open(
                self.key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            # An existing key is deliberately reused so data survives app
            # restarts.  Tighten permissions in case an older version created
            # it under a permissive umask.
            self.key_path.chmod(0o600)
            return

        try:
            with os.fdopen(key_fd, "wb") as key_file:
                key_file.write(Fernet.generate_key())
                key_file.flush()
                os.fsync(key_file.fileno())
        except BaseException:
            # Do not leave a partial key behind if a write is interrupted.
            try:
                self.key_path.unlink()
            except FileNotFoundError:
                pass
            raise
        finally:
            # chmod is intentional even though os.open receives 0600: an
            # existing umask or platform-specific defaults must not widen it.
            try:
                self.key_path.chmod(0o600)
            except FileNotFoundError:
                # The cleanup above removed an incomplete key after a failed
                # write; preserve the original exception in that case.
                pass

    def encrypt(self, value: str) -> str:
        """Return an authenticated Fernet token for ``value``."""

        if not isinstance(value, str):
            raise TypeError("SecretBox.encrypt expects a string")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Authenticate and return a Fernet token's plaintext value."""

        if not isinstance(token, str):
            raise TypeError("SecretBox.decrypt expects a string token")
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
