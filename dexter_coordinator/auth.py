"""Owner passphrase and owner signatures: the SIMULATED human approval check.

How it works:
  - The passphrase is stretched with scrypt (salted, memory-hard). Only a
    verifier derived from it is stored, never the passphrase.
  - A random 32-byte signing key is created when the owner is set up. It is
    stored wrapped (XOR with a key derived from the passphrase), so it can only
    be recovered by someone who knows the passphrase. Changing the passphrase
    re-wraps the same key, so older signatures stay checkable.
  - Every history entry made by the owner carries an HMAC-SHA256 signature
    made with that key.

What it does NOT do: it is not secure authentication. Anyone who knows or
guesses the passphrase is the owner as far as Dexter can tell. Someone who can
read the database file can try passphrases offline, at the cost of one scrypt
run per guess. Someone who can write the file can delete records, roll the
whole file back to an older copy, or replace the owner record with their own
(the real owner then finds that their passphrase no longer works). Nothing here
is connected to GitHub or any other system.
"""

import hashlib
import hmac
import json
import secrets
import unicodedata

from .models import DexterError

APPROVAL_LABEL = (
    "SIMULATED human approval: checked with a local passphrase only. "
    "This is not secure authentication and is not connected to GitHub."
)
KDF = "scrypt"
DEFAULT_COST = 2 ** 15        # scrypt N. With r=8, p=1 each check uses 32 MiB of memory.
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 128 * 1024 * 1024
MIN_PASSPHRASE_LENGTH = 12
MAX_PASSPHRASE_LENGTH = 1024
FREE_ATTEMPTS = 2             # wrong passphrases allowed before Dexter starts making you wait
MAX_LOCKOUT_SECONDS = 900

_VERIFY = b"dexter-owner-verify-v1"
_WRAP = b"dexter-owner-wrap-v1"


class AuthError(DexterError):
    """The owner passphrase was missing, wrong, locked out, or not set up."""


def check_new_passphrase(passphrase):
    if not isinstance(passphrase, str) or len(passphrase.strip()) < MIN_PASSPHRASE_LENGTH:
        raise AuthError(f"the passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters")
    if len(passphrase) > MAX_PASSPHRASE_LENGTH:
        raise AuthError("the passphrase is too long")


def _master(passphrase, salt_hex, cost):
    normalized = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return hashlib.scrypt(normalized, salt=bytes.fromhex(salt_hex), n=cost, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=SCRYPT_MAXMEM, dklen=32)


def _derive(master, label):
    return hmac.new(master, label, hashlib.sha256).digest()


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def new_owner_record(passphrase, cost=DEFAULT_COST, signing_key=None):
    """A fresh owner record. Pass the existing signing_key when changing the passphrase."""
    check_new_passphrase(passphrase)
    salt = secrets.token_hex(16)
    master = _master(passphrase, salt, cost)
    signing_key = signing_key or secrets.token_bytes(32)
    return {
        "kdf": KDF,
        "salt": salt,
        "cost": cost,
        "verifier": _derive(master, _VERIFY).hex(),
        "wrapped_key": _xor(signing_key, _derive(master, _WRAP)).hex(),
    }


def unlock(passphrase, record):
    """Return the owner's signing key if the passphrase is right, otherwise None."""
    if not isinstance(passphrase, str) or not passphrase or len(passphrase) > MAX_PASSPHRASE_LENGTH:
        return None
    if record["kdf"] != KDF:
        return None
    master = _master(passphrase, record["salt"], record["cost"])
    if not hmac.compare_digest(_derive(master, _VERIFY).hex(), record["verifier"]):
        return None
    return _xor(bytes.fromhex(record["wrapped_key"]), _derive(master, _WRAP))


def lockout_seconds(failures):
    """How long to refuse owner actions after this many wrong passphrases in a row."""
    if failures <= FREE_ATTEMPTS:
        return 0
    return min(2 ** (failures - FREE_ATTEMPTS), MAX_LOCKOUT_SECONDS)


def sign(key, fields):
    body = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def signature_matches(key, fields, mac):
    return isinstance(mac, str) and hmac.compare_digest(sign(key, fields), mac)
