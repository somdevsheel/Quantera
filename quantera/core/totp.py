"""RFC 6238 time-based one-time passwords (SHA-1, 6 digits, 30-second steps), standard library
only, and Fernet encryption of the shared secret at rest."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return str(n).zfill(DIGITS)


def code_at(secret: str, t: float) -> str:
    return _code(secret, int(t) // STEP)


def verify(secret: str, code: str, *, last_step: int | None, now: float | None = None,
           window: int = 1) -> int | None:
    """The matched time step, or None. A step at or before `last_step` is refused (no replay)."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    step = int(now if now is not None else time.time()) // STEP
    for s in range(step - window, step + window + 1):
        if (last_step is None or s > last_step) and hmac.compare_digest(_code(secret, s), code):
            return s
    return None


def uri(secret: str, account: str, issuer: str = "Quantera") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def _fernet(key_material: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        b"quantera-mfa-v1:" + key_material.encode()).digest()))


def seal(key_material: str, secret: str) -> str:
    return _fernet(key_material).encrypt(secret.encode()).decode()


def unseal(key_material: str, sealed: str) -> str:
    try:
        return _fernet(key_material).decrypt(sealed.encode()).decode()
    except InvalidToken as e:  # the key changed: MFA must be reset by an operator
        raise ValueError("MFA secret cannot be decrypted with the current key") from e
