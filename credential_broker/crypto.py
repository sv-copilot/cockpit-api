"""Symmetric encryption helpers for the credential broker."""

from __future__ import annotations

import base64
import hashlib
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_KEY_ID: Final[str] = "v1"


def fingerprint_secret(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:16]


def derive_fernet_key(master_key: str) -> bytes:
    if not master_key.strip():
        raise ValueError("COCKPIT_SECRETS_MASTER_KEY must be set")
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(value: str, *, master_key: str, key_id: str = DEFAULT_KEY_ID) -> str:
    token = Fernet(derive_fernet_key(master_key)).encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(ciphertext: str, *, master_key: str) -> str:
    try:
        plaintext = Fernet(derive_fernet_key(master_key)).decrypt(ciphertext.encode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("credential ciphertext could not be decrypted") from exc
    return plaintext.decode("utf-8")
