"""Local owner passphrase: the SIMULATED human approval check.

This is not secure authentication. Anyone who can read or edit the database
file, or who knows the passphrase, can act as the owner. It exists so that a
human decision is a deliberate, interactive step, and so that nothing an AI
writes (a response, a handoff field) can count as one. It is never connected
to GitHub or any other system.
"""

import hashlib
import hmac
import secrets

from .models import DexterError

APPROVAL_LABEL = (
    "SIMULATED human approval: checked with a local passphrase only. "
    "This is not secure authentication and is not connected to GitHub."
)
MIN_PASSPHRASE_LENGTH = 8
MAX_PASSPHRASE_LENGTH = 1024
DEFAULT_ITERATIONS = 310_000


class AuthError(DexterError):
    """The owner passphrase was missing, wrong, or not set up."""


def check_new_passphrase(passphrase):
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise AuthError(f"the passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters")
    if len(passphrase) > MAX_PASSPHRASE_LENGTH:
        raise AuthError("the passphrase is too long")


def hash_passphrase(passphrase, salt_hex, iterations):
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
    return digest.hex()


def new_owner_record(passphrase, iterations=DEFAULT_ITERATIONS):
    check_new_passphrase(passphrase)
    salt = secrets.token_hex(16)
    return {"salt": salt, "hash": hash_passphrase(passphrase, salt, iterations), "iterations": iterations}


def passphrase_matches(passphrase, record):
    if not isinstance(passphrase, str) or not passphrase or len(passphrase) > MAX_PASSPHRASE_LENGTH:
        return False
    candidate = hash_passphrase(passphrase, record["salt"], record["iterations"])
    return hmac.compare_digest(candidate, record["hash"])
