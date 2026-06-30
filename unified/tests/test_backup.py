import pytest

from unified import backup, vault


MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
PASSWORD = "correct horse battery staple"


def test_wallet_backup_round_trip_excludes_resyncable_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    vault.create_vault(str(src / "vault.enc"), MNEMONIC, PASSWORD)
    wallets = src / "blc" / "wallets"
    wallets.mkdir(parents=True)
    (wallets / "default_wallet").write_text('{"lightning_privkey2":"kept"}', encoding="utf-8")
    (src / "contacts.json").write_text('{"contacts":[]}', encoding="utf-8")
    (src / "blc" / "config").write_text('{"rpcport":57101}', encoding="utf-8")
    (src / "blc" / "cache").mkdir()
    (src / "blc" / "cache" / "junk").write_text("skip", encoding="utf-8")
    with open(src / "blc" / "blockchain_headers", "wb") as f:
        f.seek(2 * 1024 * 1024)
        f.write(b"x")

    out = tmp_path / "wallet.bswallet"
    result = backup.create_backup(str(src), str(out), PASSWORD)
    assert result["ok"] is True
    assert out.stat().st_size < 1024 * 1024

    dst = tmp_path / "dst"
    restored = backup.restore_backup(str(out), str(dst), PASSWORD)
    assert restored["ok"] is True
    assert vault.unlock_vault(str(dst / "vault.enc"), PASSWORD) == MNEMONIC
    assert (dst / "blc" / "wallets" / "default_wallet").read_text(encoding="utf-8") == '{"lightning_privkey2":"kept"}'
    assert not (dst / "blc" / "blockchain_headers").exists()
    assert not (dst / "blc" / "cache" / "junk").exists()


def test_wallet_backup_rejects_wrong_password(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    vault.create_vault(str(src / "vault.enc"), MNEMONIC, PASSWORD)
    out = tmp_path / "wallet.bswallet"
    backup.create_backup(str(src), str(out), PASSWORD)

    with pytest.raises(backup.BackupError):
        backup.restore_backup(str(out), str(tmp_path / "dst"), "wrong password")
