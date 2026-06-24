"""P3 vault tests: Argon2id -> AES-256-GCM round-trip, wrong password, tamper,
password change, file hygiene."""

import json
import os

import pytest

from unified import vault

MNEMONIC = ("abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon abandon abandon about")
PW = "correct horse battery staple"


def test_create_unlock_roundtrip(tmp_path):
    path = str(tmp_path / "vault.enc")
    vault.create_vault(path, MNEMONIC, PW)
    assert vault.vault_exists(path)
    assert vault.unlock_vault(path, PW) == MNEMONIC


def test_wrong_password_rejected(tmp_path):
    path = str(tmp_path / "vault.enc")
    vault.create_vault(path, MNEMONIC, PW)
    with pytest.raises(vault.BadPassword):
        vault.unlock_vault(path, "wrong password")


def test_tamper_detected(tmp_path):
    path = str(tmp_path / "vault.enc")
    vault.create_vault(path, MNEMONIC, PW)
    blob = json.loads(open(path).read())
    ct = bytearray(vault._unb64(blob["ciphertext"]))
    ct[0] ^= 0x01  # flip a bit
    blob["ciphertext"] = vault._b64(bytes(ct))
    open(path, "w").write(json.dumps(blob))
    with pytest.raises(vault.BadPassword):
        vault.unlock_vault(path, PW)


def test_change_password(tmp_path):
    path = str(tmp_path / "vault.enc")
    vault.create_vault(path, MNEMONIC, PW)
    vault.change_password(path, PW, "new-pass-123")
    with pytest.raises(vault.BadPassword):
        vault.unlock_vault(path, PW)            # old no longer works
    assert vault.unlock_vault(path, "new-pass-123") == MNEMONIC  # same seed, new pw


def test_no_plaintext_on_disk_and_perms(tmp_path):
    path = str(tmp_path / "vault.enc")
    vault.create_vault(path, MNEMONIC, PW)
    raw = open(path, "rb").read()
    assert b"abandon" not in raw and b"about" not in raw
    blob = json.loads(raw)
    assert blob["kdf"] == "argon2id" and blob["version"] == vault.VERSION
    assert set(("salt", "nonce", "ciphertext", "time_cost", "memory_cost", "parallelism")) <= set(blob)
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_distinct_salt_nonce_per_create(tmp_path):
    p1 = str(tmp_path / "v1.enc"); p2 = str(tmp_path / "v2.enc")
    vault.create_vault(p1, MNEMONIC, PW)
    vault.create_vault(p2, MNEMONIC, PW)
    b1, b2 = json.loads(open(p1).read()), json.loads(open(p2).read())
    assert b1["salt"] != b2["salt"] and b1["nonce"] != b2["nonce"]
    assert b1["ciphertext"] != b2["ciphertext"]  # same seed encrypts differently
