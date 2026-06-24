"""P3 — encrypted seed vault (Argon2id -> AES-256-GCM).

The user's password is the real gate. The vault file stores ONLY
``{version, kdf params, salt, nonce, ciphertext}`` — never the plaintext mnemonic.
The launcher/orchestrator unlocks the vault to source the master mnemonic instead
of reading it from stdin or generating it ad-hoc.

Correctness notes (these bite if you copy a naive sketch):
  * derive a RAW 32-byte key with ``argon2.low_level.hash_secret_raw`` (type=ID) —
    NOT ``argon2.hash`` (which returns a PHC *string*, not a 32-byte key, and would
    break AES-256).
  * AES-256-GCM authenticates: a wrong password (or any tampering) raises
    ``InvalidTag`` on decrypt, surfaced here as a clean ``BadPassword``.
  * Python cannot guarantee zeroing memory (``str``/``bytes`` are immutable). We
    minimise the decrypted seed's lifetime and zero the mutable buffers we control;
    treat secure-wipe as best-effort.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
import unicodedata
from typing import Optional

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
AAD = b"blakestream-electrum-vault-v1"

# Argon2id parameters: desktop-friendly but strong (64 MiB, t=3, p=4).
KDF_TIME_COST = 3
KDF_MEMORY_KIB = 64 * 1024
KDF_PARALLELISM = 4
KEY_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12

# Bounds enforced on the params READ FROM the vault file at unlock. A tampered
# vault must not be able to weaken the KDF (downgrade) nor request an absurd memory
# cost that OOM-crashes the app (DoS). Floor = the values we write; ceiling caps
# resource use.
KDF_MEMORY_KIB_MAX = 1024 * 1024  # 1 GiB
KDF_TIME_COST_MAX = 24
KDF_PARALLELISM_MAX = 16


class BadPassword(Exception):
    """Wrong password or corrupt/tampered vault."""


def _derive_key(password: str, salt: bytes, *, time_cost: int, memory_cost: int,
                parallelism: int) -> bytes:
    # NFKD-normalize so the same typed password derives the same key regardless of
    # unicode composition / input method (matches BIP39/Electrum seed handling).
    normalized = unicodedata.normalize("NFKD", password)
    return hash_secret_raw(
        secret=normalized.encode("utf-8"), salt=salt,
        time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism,
        hash_len=KEY_LEN, type=Type.ID)


def _validate_kdf_params(blob: dict):
    """Reject a vault whose stored KDF params are tampered/out-of-range, so the
    attacker can neither downgrade the work factor nor trigger an OOM. Returns the
    params as ints (so a string/float never reaches argon2 raw -> TypeError). Treated
    as a corrupt vault (BadPassword) rather than leaking that it was tampered."""
    if blob.get("kdf") != "argon2id":
        raise BadPassword("unsupported or tampered vault kdf")
    try:
        tc, mc, par = int(blob["time_cost"]), int(blob["memory_cost"]), int(blob["parallelism"])
    except (KeyError, TypeError, ValueError):
        raise BadPassword("corrupt vault kdf params")
    if not (KDF_TIME_COST <= tc <= KDF_TIME_COST_MAX):
        raise BadPassword("vault time_cost out of range")
    if not (KDF_MEMORY_KIB <= mc <= KDF_MEMORY_KIB_MAX):
        raise BadPassword("vault memory_cost out of range")
    if not (1 <= par <= KDF_PARALLELISM_MAX):
        raise BadPassword("vault parallelism out of range")
    return tc, mc, par


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _zero(buf: bytearray) -> None:
    for i in range(len(buf)):
        buf[i] = 0


def _fsync_dir(directory: str) -> None:
    """fsync a directory so a completed rename survives a crash (POSIX only —
    Windows has no directory fd; there ``os.replace`` is still atomic)."""
    if not directory or not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(directory, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---- at-rest encryption keys (wallet files + contacts) -------------------------------
# Per-coin wallet-encryption passwords and a contacts key, derived from the SEED via
# HKDF-SHA256 with per-purpose info strings. Deriving from the seed (not the vault
# password) means a password change never requires re-encrypting anything. Each wallet
# password is a 256-bit high-entropy hex string, so Electrum's pbkdf2(1024) over it has
# no brute-force surface (the weak KDF is irrelevant against a random 256-bit input).
_HKDF_SALT = b"blakestream-electrum-keys-v1"
CONTACTS_AAD = b"blakestream-electrum-contacts-v1"
CONTACTS_MAGIC = b"BSC1"   # marks an encrypted contacts blob (vs legacy plaintext JSON)


def _hkdf(ikm: bytes, info: bytes, length: int = KEY_LEN) -> bytes:
    """HKDF-SHA256 (RFC 5869 extract+expand) over high-entropy seed material."""
    prk = hmac.new(_HKDF_SALT, ikm, hashlib.sha256).digest()
    out, t, counter = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]


def derive_session_keys(mnemonic: str, tickers):
    """Return ({TICKER: wallet_password_hex}, contacts_key_bytes) derived from the seed.
    Per-coin domain separation (info="wallet:<TICKER>:v1") so one coin's wallet password
    can never open another's."""
    ikm = unicodedata.normalize("NFKD", mnemonic).encode("utf-8")
    wallet_pws = {t: _hkdf(ikm, b"wallet:" + t.encode("ascii") + b":v1").hex() for t in tickers}
    contacts_key = _hkdf(ikm, b"contacts:v1")
    return wallet_pws, contacts_key


def encrypt_blob(plaintext: bytes, key: bytes) -> bytes:
    """AEAD-encrypt arbitrary bytes (used for contacts.json) -> magic||nonce||ciphertext."""
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, CONTACTS_AAD)
    return CONTACTS_MAGIC + nonce + ct


def is_encrypted_blob(blob: bytes) -> bool:
    return blob[:len(CONTACTS_MAGIC)] == CONTACTS_MAGIC


def decrypt_blob(blob: bytes, key: bytes) -> bytes:
    if not is_encrypted_blob(blob):
        raise BadPassword("not an encrypted contacts blob")
    off = len(CONTACTS_MAGIC)
    nonce, ct = blob[off:off + NONCE_LEN], blob[off + NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, CONTACTS_AAD)
    except InvalidTag:
        raise BadPassword("contacts decryption failed (wrong key or tampered)")


def create_vault(path: str, mnemonic: str, password: str) -> None:
    """Encrypt ``mnemonic`` under ``password`` and write the vault atomically (0600)."""
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = bytearray(_derive_key(password, salt, time_cost=KDF_TIME_COST,
                                memory_cost=KDF_MEMORY_KIB, parallelism=KDF_PARALLELISM))
    try:
        # AAD binds the ciphertext to this vault's own salt (defence-in-depth).
        ciphertext = AESGCM(bytes(key)).encrypt(nonce, mnemonic.encode("utf-8"), AAD + salt)
    finally:
        _zero(key)
    blob = {
        "version": VERSION, "kdf": "argon2id",
        "time_cost": KDF_TIME_COST, "memory_cost": KDF_MEMORY_KIB,
        "parallelism": KDF_PARALLELISM,
        "salt": _b64(salt), "nonce": _b64(nonce), "ciphertext": _b64(ciphertext),
    }
    # Durable atomic write to a UNIQUE owner-only temp (mkstemp creates it 0600 from the
    # start — no 0664 window, no shared-".tmp" collision between concurrent callers), then
    # fsync, swap in atomically, and fsync the dir so the rename survives a crash.
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".vault-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(blob, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
    _fsync_dir(directory)


def unlock_vault(path: str, password: str) -> str:
    """Return the decrypted mnemonic, or raise :class:`BadPassword`."""
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
    except (ValueError, UnicodeDecodeError):   # truncated / non-JSON / non-UTF-8 -> corrupt
        raise BadPassword("corrupt vault")
    if not isinstance(blob, dict):
        raise BadPassword("corrupt vault")
    # Strict: a float 1.0 must NOT slip past (1.0 == 1 in Python); require exactly int 1.
    if type(blob.get("version")) is not int or blob.get("version") != VERSION:
        raise ValueError(f"unsupported vault version: {blob.get('version')!r}")
    tc, mc, par = _validate_kdf_params(blob)
    # Any structural damage (missing field, bad base64, wrong lengths) is a corrupt
    # vault -> BadPassword, not an uncaught decode error.
    try:
        salt = _unb64(blob["salt"])
        nonce = _unb64(blob["nonce"])
        ciphertext = _unb64(blob["ciphertext"])
    except (KeyError, ValueError, TypeError):
        raise BadPassword("corrupt vault")
    if len(salt) != SALT_LEN or len(nonce) != NONCE_LEN:
        raise BadPassword("corrupt vault salt/nonce length")
    key = bytearray(_derive_key(password, salt, time_cost=tc, memory_cost=mc, parallelism=par))
    try:
        plaintext = AESGCM(bytes(key)).decrypt(nonce, ciphertext, AAD + salt)
    except InvalidTag:
        raise BadPassword("wrong password or corrupt vault")
    finally:
        _zero(key)
    return plaintext.decode("utf-8")


def change_password(path: str, old_password: str, new_password: str) -> None:
    """Re-encrypt the same mnemonic under a new password (seed never hits disk in
    plaintext)."""
    mnemonic = unlock_vault(path, old_password)
    try:
        create_vault(path, mnemonic, new_password)
    finally:
        del mnemonic


def vault_exists(path: str) -> bool:
    return os.path.isfile(path)
