"""Encrypted full-wallet backup bundle for the unified multiwallet.

The live wallet stays as a directory tree because the six Electrum daemons expect
their own datadirs. This module packages the portable state into one encrypted
``.bswallet`` file for users.
"""

from __future__ import annotations

import base64
import io
import json
import os
import posixpath
import shutil
import tempfile
import time
import zipfile
from typing import Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from unified import vault

MAGIC = b"BSWALLET1\n"
VERSION = 1
AAD = b"blakestream-wallet-backup-v1"

EXCLUDE_DIRS = {
    "__pycache__",
    "cache",
    "certs",
    "forks",
    "logs",
    "plugins",
}
EXCLUDE_FILES = {
    "blockchain_headers",
    "gossip_db",
    "daemon",
    "orchestrator.start.lock",
}
EXCLUDE_SUFFIXES = (
    ".lock",
    ".pid",
    ".sock",
    ".tmp",
)


class BackupError(Exception):
    """Human-facing backup/restore error."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _iter_backup_files(root: str) -> Iterable[tuple[str, str]]:
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".restore-"))
        for name in sorted(filenames):
            if name in EXCLUDE_FILES or name.endswith(EXCLUDE_SUFFIXES):
                continue
            path = os.path.join(dirpath, name)
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            rel = os.path.relpath(path, root)
            if rel.startswith(".."):
                continue
            yield path, rel.replace(os.sep, "/")


def _safe_archive_name(name: str) -> str:
    name = name.replace("\\", "/")
    if name.startswith("/") or name.startswith("../") or "/../" in name or name in ("", ".", ".."):
        raise BackupError(f"unsafe backup path: {name!r}")
    clean = posixpath.normpath(name)
    if clean.startswith("../") or clean in ("", ".", ".."):
        raise BackupError(f"unsafe backup path: {name!r}")
    return clean


def _derive(password: str, salt: bytes) -> bytes:
    return vault._derive_key(
        password,
        salt,
        time_cost=vault.KDF_TIME_COST,
        memory_cost=vault.KDF_MEMORY_KIB,
        parallelism=vault.KDF_PARALLELISM,
    )


def _build_zip(datadirs_root: str) -> bytes:
    datadirs_root = os.path.abspath(datadirs_root)
    if not os.path.isdir(datadirs_root):
        raise BackupError("wallet data directory does not exist")
    files = list(_iter_backup_files(datadirs_root))
    if not any(rel == "vault.enc" for _, rel in files):
        raise BackupError("wallet vault not found")
    manifest = {
        "version": VERSION,
        "created_at": int(time.time()),
        "format": "blakestream-wallet-backup",
        "excluded": {
            "dirs": sorted(EXCLUDE_DIRS),
            "files": sorted(EXCLUDE_FILES),
            "suffixes": list(EXCLUDE_SUFFIXES),
        },
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
        for src, rel in files:
            zf.write(src, rel)
    return buf.getvalue()


def create_backup(datadirs_root: str, output_path: str, password: str) -> dict:
    """Create one encrypted ``.bswallet`` file.

    The caller must already have verified ``password`` against the current vault.
    """
    if not output_path:
        raise BackupError("backup path is required")
    output_path = os.path.abspath(output_path)
    datadirs_root = os.path.abspath(datadirs_root)
    try:
        common = os.path.commonpath([datadirs_root, output_path])
    except ValueError:
        common = ""
    if common == datadirs_root:
        raise BackupError("choose a backup location outside the wallet data folder")

    plaintext = _build_zip(datadirs_root)
    salt = os.urandom(vault.SALT_LEN)
    nonce = os.urandom(vault.NONCE_LEN)
    key = bytearray(_derive(password, salt))
    try:
        ciphertext = AESGCM(bytes(key)).encrypt(nonce, plaintext, AAD + salt)
    finally:
        vault._zero(key)
    header = {
        "version": VERSION,
        "kdf": "argon2id",
        "time_cost": vault.KDF_TIME_COST,
        "memory_cost": vault.KDF_MEMORY_KIB,
        "parallelism": vault.KDF_PARALLELISM,
        "salt": _b64(salt),
        "nonce": _b64(nonce),
    }
    header_raw = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = MAGIC + len(header_raw).to_bytes(4, "big") + header_raw + ciphertext

    directory = os.path.dirname(output_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bswallet-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, output_path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    try:
        os.chmod(output_path, 0o600)
    except OSError:
        pass
    return {"ok": True, "path": output_path, "bytes": len(payload), "files": len(list(_iter_backup_files(datadirs_root)))}


def _decrypt_backup(input_path: str, password: str) -> bytes:
    try:
        raw = open(input_path, "rb").read()
    except OSError as e:
        raise BackupError(f"could not read backup: {e}")
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
        raise BackupError("not a Blakestream wallet backup")
    off = len(MAGIC)
    header_len = int.from_bytes(raw[off:off + 4], "big")
    off += 4
    if header_len < 2 or header_len > 16 * 1024 or len(raw) < off + header_len:
        raise BackupError("corrupt backup header")
    try:
        header = json.loads(raw[off:off + header_len].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BackupError("corrupt backup header")
    off += header_len
    if header.get("version") != VERSION or header.get("kdf") != "argon2id":
        raise BackupError("unsupported backup version")
    try:
        salt = _unb64(header["salt"])
        nonce = _unb64(header["nonce"])
    except (KeyError, ValueError, TypeError):
        raise BackupError("corrupt backup key data")
    if len(salt) != vault.SALT_LEN or len(nonce) != vault.NONCE_LEN:
        raise BackupError("corrupt backup key data")
    key = bytearray(_derive(password, salt))
    try:
        return AESGCM(bytes(key)).decrypt(nonce, raw[off:], AAD + salt)
    except InvalidTag:
        raise BackupError("wrong backup password or corrupt backup")
    finally:
        vault._zero(key)


def restore_backup(input_path: str, datadirs_root: str, password: str) -> dict:
    """Restore an encrypted backup into an empty/no-vault data directory."""
    plaintext = _decrypt_backup(input_path, password)
    datadirs_root = os.path.abspath(datadirs_root)
    os.makedirs(datadirs_root, exist_ok=True)

    try:
        zf = zipfile.ZipFile(io.BytesIO(plaintext), "r")
    except zipfile.BadZipFile:
        raise BackupError("corrupt backup archive")
    parent = os.path.dirname(datadirs_root) or "."
    tmp_root = tempfile.mkdtemp(prefix=".restore-", dir=parent)
    restored = 0
    try:
        with zf:
            names = [_safe_archive_name(i.filename) for i in zf.infolist() if not i.is_dir()]
            if "manifest.json" not in names or "vault.enc" not in names:
                raise BackupError("backup is missing required wallet data")
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = _safe_archive_name(info.filename)
                dst = os.path.join(tmp_root, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with zf.open(info, "r") as src, open(dst, "wb") as out:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                try:
                    os.chmod(dst, 0o600)
                except OSError:
                    pass
                restored += 1
        try:
            vault.unlock_vault(os.path.join(tmp_root, "vault.enc"), password)
        except vault.BadPassword:
            raise BackupError("backup decrypted, but its wallet vault does not match this password")
        except ValueError as e:
            raise BackupError(str(e)[:120])

        for dirpath, dirnames, filenames in os.walk(tmp_root):
            rel_dir = os.path.relpath(dirpath, tmp_root)
            out_dir = datadirs_root if rel_dir == "." else os.path.join(datadirs_root, rel_dir)
            os.makedirs(out_dir, exist_ok=True)
            try:
                os.chmod(out_dir, 0o700)
            except OSError:
                pass
            for name in filenames:
                src = os.path.join(dirpath, name)
                dst = os.path.join(out_dir, name)
                shutil.copyfile(src, dst)
                try:
                    os.chmod(dst, 0o600)
                except OSError:
                    pass
            dirnames.sort()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    for dirpath, dirnames, _filenames in os.walk(datadirs_root):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass
        dirnames.sort()
    return {"ok": True, "files": restored}
