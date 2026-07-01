"""Contacts (address book) for the multicoin wallet.

A single address book spanning all six coins (each entry tagged with its coin), kept as a
``0600`` ``contacts.json`` sidecar in the vault directory — the same owner-only dir family as
the vault and per-coin datadirs. Privacy-sensitive (who you transact with), so when the unlocked
session provides a key it is stored **encrypted at rest** (AEAD, seed-derived key) — otherwise it
falls back to plaintext JSON. Writes are atomic (mkstemp -> fsync -> replace) under a process
lock, with an in-memory cache so reads do not hit disk on every API call.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading

from unified import vault

_VERSION = 1
_lock = threading.Lock()
_cache: dict = {}   # path -> parsed dict (decrypted, in memory)


def _empty() -> dict:
    return {"version": _VERSION, "contacts": []}


def _read(path: str, key=None) -> dict:
    """Load the address book. Decrypts an encrypted blob when a key is supplied; reads legacy
    plaintext JSON otherwise. Never raises — a missing/corrupt/undecryptable file reads empty."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if vault.is_encrypted_blob(raw):
            if key is None:
                return _empty()        # encrypted but no key in scope -> can't read
            raw = vault.decrypt_blob(raw, key)
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("contacts"), list):
            return data
    except Exception:
        pass
    return _empty()


def _on_disk_is_plaintext(path: str) -> bool:
    """True iff a contacts file exists on disk and is NOT an encrypted blob (legacy plaintext)."""
    try:
        with open(path, "rb") as f:
            return not vault.is_encrypted_blob(f.read(8))
    except OSError:
        return False   # missing -> nothing to migrate


def _atomic_write(path: str, data: dict, key=None) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    payload = json.dumps(data, indent=2).encode("utf-8")
    if key is not None:
        payload = vault.encrypt_blob(payload, key)   # encrypt at rest when unlocked
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".contacts.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        try:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _maybe_migrate(path: str, data: dict, key) -> None:
    """One-time: re-encrypt a legacy plaintext contacts.json once a session key is available."""
    if key is not None and _on_disk_is_plaintext(path):
        try:
            _atomic_write(path, data, key)
        except Exception:
            pass


def list_contacts(path: str, coin: str | None = None, key=None) -> list:
    with _lock:
        data = _cache.get(path) or _read(path, key)
        _cache[path] = data
        _maybe_migrate(path, data, key)
        items = list(data.get("contacts", []))
    if coin:
        items = [c for c in items if (c.get("coin") or "").upper() == coin.upper()]
    return items


def add(path: str, coin: str, address: str, label: str, key=None) -> dict:
    coin = (coin or "").upper().strip()
    address = (address or "").strip()
    label = (label or "").strip()
    if not coin or not address:
        raise ValueError("coin and address are required")
    with _lock:
        data = _cache.get(path) or _read(path, key)
        contacts = data.setdefault("contacts", [])
        # dedup on (coin, address): update the label instead of adding a duplicate
        for c in contacts:
            if (c.get("coin") or "").upper() == coin and (c.get("address") or "") == address:
                c["label"] = label
                _atomic_write(path, data, key)
                _cache[path] = data
                return c
        contact = {"id": "c_" + secrets.token_hex(6), "coin": coin,
                   "address": address, "label": label}
        contacts.append(contact)
        _atomic_write(path, data, key)
        _cache[path] = data
        return contact


def delete(path: str, contact_id: str, key=None) -> bool:
    with _lock:
        data = _cache.get(path) or _read(path, key)
        contacts = data.get("contacts", [])
        before = len(contacts)
        data["contacts"] = [c for c in contacts if c.get("id") != contact_id]
        removed = len(data["contacts"]) < before
        if removed:
            _atomic_write(path, data, key)
            _cache[path] = data
        return removed
