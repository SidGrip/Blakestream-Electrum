#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


COPY_IGNORE_NAMES = {
    ".git",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    "build",
    ".build-multi",   # multiwallet build root lives in-repo; never copy it into a workspace
    "dist",
    "outputs",
    "qa-wallet-src",
    "coin-overlays",
    "scripts",
    "node_modules",
    "__pycache__",
    "Electrum.egg-info",
}

REQUIRED_SUBMODULE_PATHS = (
    "electrum/locale",
    "electrum/plugins/keepkey/keepkeylib",
)


def load_coin(repo_root: Path, coin_code: str) -> dict:
    with (repo_root / "coin-overlays" / "coins.json").open("r", encoding="utf-8") as fh:
        coins = json.load(fh)
    try:
        return coins[coin_code.upper()]
    except KeyError as exc:
        raise SystemExit(f"unknown coin code: {coin_code}") from exc


def copy_ignore(_dir: str, names: list[str]) -> set[str]:
    ignored = set()
    parts = Path(_dir).parts
    in_desktop_root = len(parts) >= 2 and parts[-2:] == ("unified", "desktop")
    for name in names:
        if name in COPY_IGNORE_NAMES or name.startswith(".venv"):
            ignored.add(name)
        elif in_desktop_root and name in {"backend", "release"}:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo")):
            ignored.add(name)
        elif name.startswith("_blake256.") and name.endswith(".so"):
            ignored.add(name)
    return ignored


def replace_or_fail(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def regex_replace(path: Path, pattern: str, repl: str, *, count: int = 0) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(pattern, repl, text, count=count, flags=re.MULTILINE)
    if n == 0:
        raise RuntimeError(f"pattern not found in {path}: {pattern}")
    path.write_text(new_text, encoding="utf-8")


def replace_class_attr(text: str, class_name: str, attr: str, value: str) -> str:
    pattern = rf"(class {class_name}\([^)]*\):[\s\S]*?^\s+{re.escape(attr)}\s*=\s*).*$"
    new_text, count = re.subn(pattern, rf"\g<1>{value}", text, count=1, flags=re.MULTILINE)
    if count == 0:
        raise RuntimeError(f"could not patch {class_name}.{attr}")
    return new_text


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def replace_many(path: Path, replacements: list[tuple[str, str]]) -> None:
    original = path.read_text(encoding="utf-8")
    # Strict: every source string must be present, or branding would silently not apply.
    # Checked against the unmodified text so order-dependent substrings don't false-positive.
    missing = [old for old, _ in replacements if old not in original]
    if missing:
        raise RuntimeError(
            f"branding source string(s) not found in {path}:\n  "
            + "\n  ".join(repr(m) for m in missing))
    text = original
    for old, new in replacements:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def copy_source_tree(repo_root: Path, workspace: Path) -> None:
    ensure_required_submodules(repo_root)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo_root, workspace, ignore=copy_ignore)


def ensure_required_submodules(repo_root: Path) -> None:
    if not missing_required_submodules(repo_root):
        return

    lock_dir = repo_root / "build" / "locks" / "submodules.lockd"
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 600
    have_lock = False
    while not have_lock:
        try:
            lock_dir.mkdir()
            have_lock = True
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"timed out waiting for submodule lock: {lock_dir}")
            time.sleep(1)

    try:
        if missing_required_submodules(repo_root):
            subprocess.run(
                ["git", "-C", str(repo_root), "submodule", "update", "--init", "--depth", "1", *REQUIRED_SUBMODULE_PATHS],
                check=True,
            )
    finally:
        if have_lock:
            os.rmdir(lock_dir)


def missing_required_submodules(repo_root: Path) -> list[str]:
    missing = []
    for path in REQUIRED_SUBMODULE_PATHS:
        full_path = repo_root / path
        if not full_path.is_dir() or not any(full_path.iterdir()):
            missing.append(path)
    return missing


def copy_overlay_assets(repo_root: Path, workspace: Path, coin_code: str, coin: dict) -> None:
    source_icons = repo_root / "coin-overlays" / coin_code / "icons"
    target_icons = workspace / "electrum" / "gui" / "icons"
    if not source_icons.is_dir():
        raise RuntimeError(f"missing icon overlay: {source_icons}")
    for src in source_icons.iterdir():
        if src.is_file():
            shutil.copy2(src, target_icons / src.name)

    app_slug_icon = target_icons / f"{coin['app_slug']}.png"
    legacy_overlay_icon = target_icons / "electrum-blc.png"
    bitcoin_icon = target_icons / "bitcoin.png"
    if legacy_overlay_icon.exists():
        shutil.copy2(legacy_overlay_icon, target_icons / "electrum.png")
        if app_slug_icon.resolve() != legacy_overlay_icon.resolve():
            shutil.copy2(legacy_overlay_icon, app_slug_icon)
    elif bitcoin_icon.exists():
        shutil.copy2(bitcoin_icon, target_icons / "electrum.png")
        if app_slug_icon.resolve() != bitcoin_icon.resolve():
            shutil.copy2(bitcoin_icon, app_slug_icon)


def clear_network_files(workspace: Path, coin: dict, repo_root: Path, coin_code: str) -> None:
    chains = workspace / "electrum" / "chains"
    cp_src = repo_root / "coin-overlays" / coin_code / "checkpoints.json"
    for net_name in ("mainnet", "testnet", "testnet4", "regtest", "signet", "mutinynet"):
        net_dir = chains / net_name
        if net_name == "mainnet":
            # Ship the Blakestream ElectrumX servers, on THIS coin's ports, as the
            # default server list so the wallet connects out of the box.
            entry = {"pruning": "-", "s": coin["electrum_port_ssl"],
                     "t": coin["electrum_port_tcp"], "version": "1.4"}
            write_json(net_dir / "servers.json", {
                "electrum1.blakecoin.org": entry,
                "electrum2.blakecoin.org": entry,
                "electrum1.blakestream.io": entry,
                "electrum2.blakestream.io": entry,
            })
            # Ship activation-region checkpoints (anchor the below-auxpow-activation
            # region the wallet would otherwise trust). Falls back to [] if none.
            if cp_src.is_file():
                net_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cp_src, net_dir / "checkpoints.json")
            else:
                write_json(net_dir / "checkpoints.json", [])
        else:
            write_json(net_dir / "servers.json", {})
            write_json(net_dir / "checkpoints.json", [])
        write_json(net_dir / "fallback_lnnodes.json", {})


def patch_constants(workspace: Path, coin: dict) -> None:
    path = workspace / "electrum" / "constants.py"
    text = path.read_text(encoding="utf-8")

    text = replace_class_attr(text, "BitcoinMainnet", "WIF_PREFIX", f"0x{coin['wif_prefix']:02x}")
    text = replace_class_attr(text, "BitcoinMainnet", "ADDRTYPE_P2PKH", str(coin["p2pkh"]))
    text = replace_class_attr(text, "BitcoinMainnet", "ADDRTYPE_P2SH", str(coin["p2sh"]))
    text = replace_class_attr(text, "BitcoinMainnet", "SEGWIT_HRP", f'"{coin["segwit_hrp"]}"')
    text = replace_class_attr(text, "BitcoinMainnet", "BOLT11_HRP", "SEGWIT_HRP")
    text = replace_class_attr(text, "BitcoinMainnet", "GENESIS", f'"{coin["genesis"]}"')
    text = replace_class_attr(text, "BitcoinMainnet", "BIP44_COIN_TYPE", str(coin["coin_type"]))
    text = replace_class_attr(text, "BitcoinMainnet", "DEFAULT_PORTS",
                              "{'t': '%s', 's': '%s'}" % (coin["electrum_port_tcp"], coin["electrum_port_ssl"]))
    text = replace_class_attr(text, "BitcoinMainnet", "AUXPOW_START_HEIGHT",
                              str(coin["auxpow_start_height"]))
    text = replace_class_attr(text, "BitcoinMainnet", "AUXPOW_CHAIN_ID",
                              str(coin["auxpow_chain_id"]))

    text = replace_class_attr(text, "BitcoinTestnet", "WIF_PREFIX", f"0x{coin['testnet_wif_prefix']:02x}")
    text = replace_class_attr(text, "BitcoinTestnet", "ADDRTYPE_P2PKH", str(coin["testnet_p2pkh"]))
    text = replace_class_attr(text, "BitcoinTestnet", "ADDRTYPE_P2SH", str(coin["testnet_p2sh"]))
    text = replace_class_attr(text, "BitcoinTestnet", "SEGWIT_HRP", f'"{coin["testnet_segwit_hrp"]}"')
    text = replace_class_attr(text, "BitcoinTestnet", "BOLT11_HRP", "SEGWIT_HRP")
    text = replace_class_attr(text, "BitcoinTestnet", "GENESIS", f'"{coin["testnet_genesis"]}"')

    text = replace_class_attr(text, "BitcoinTestnet4", "GENESIS", f'"{coin["testnet_genesis"]}"')

    text = replace_class_attr(text, "BitcoinRegtest", "WIF_PREFIX", f"0x{coin['regtest_wif_prefix']:02x}")
    text = replace_class_attr(text, "BitcoinRegtest", "ADDRTYPE_P2PKH", str(coin["regtest_p2pkh"]))
    text = replace_class_attr(text, "BitcoinRegtest", "ADDRTYPE_P2SH", str(coin["regtest_p2sh"]))
    text = replace_class_attr(text, "BitcoinRegtest", "SEGWIT_HRP", f'"{coin["regtest_segwit_hrp"]}"')
    text = replace_class_attr(text, "BitcoinRegtest", "BOLT11_HRP", "SEGWIT_HRP")
    text = replace_class_attr(text, "BitcoinRegtest", "GENESIS", f'"{coin["regtest_genesis"]}"')

    path.write_text(text, encoding="utf-8")
    validate_constants(path, coin)


def _class_attr_value(text: str, class_name: str, attr: str) -> str | None:
    m = re.search(rf"class {class_name}\([^)]*\):[\s\S]*?^\s+{re.escape(attr)}\s*=\s*(.+)$",
                  text, flags=re.MULTILINE)
    return m.group(1).strip() if m else None


def validate_constants(path: Path, coin: dict) -> None:
    """Re-read the patched constants and assert every fund-critical MAINNET value equals
    coins.json. A wrong/stale coin_type or address prefix silently moves funds, so a
    value-level check (not just 'the regex matched') guards against a no-op patch or a
    stale workspace baked with old values."""
    text = path.read_text(encoding="utf-8")
    expect = {
        "WIF_PREFIX": f"0x{coin['wif_prefix']:02x}",
        "ADDRTYPE_P2PKH": str(coin["p2pkh"]),
        "ADDRTYPE_P2SH": str(coin["p2sh"]),
        "SEGWIT_HRP": f'"{coin["segwit_hrp"]}"',
        "BIP44_COIN_TYPE": str(coin["coin_type"]),
        "GENESIS": f'"{coin["genesis"]}"',
    }
    mismatched = {
        attr: {"got": _class_attr_value(text, "BitcoinMainnet", attr), "want": want}
        for attr, want in expect.items()
        if _class_attr_value(text, "BitcoinMainnet", attr) != want
    }
    if mismatched:
        raise RuntimeError(
            f"{coin['ticker']} constants validation FAILED (no-op patch or stale "
            f"workspace — would mint wrong-chain addresses): {mismatched}")


def patch_bitcoin(workspace: Path, coin: dict) -> None:
    path = workspace / "electrum" / "bitcoin.py"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^COINBASE_MATURITY = .*$", f"COINBASE_MATURITY = {coin['coinbase_maturity']}", text, flags=re.MULTILINE)
    text = re.sub(r"^TOTAL_COIN_SUPPLY_LIMIT_IN_BTC = .*$", f"TOTAL_COIN_SUPPLY_LIMIT_IN_BTC = {coin['max_supply_btc']}", text, flags=re.MULTILINE)
    text = text.replace("invalid blakecoin address", f"invalid {coin['uri_scheme']} address")
    text = text.replace("Blakecoin Signed Message", f"{coin['coin_name']} Signed Message")
    path.write_text(text, encoding="utf-8")


def patch_util(workspace: Path, coin: dict) -> None:
    ticker = coin["ticker"]
    path = workspace / "electrum" / "util.py"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^base_units = .*$", f"base_units = {{'{ticker}':8, 'm{ticker}':5, 'u{ticker}':2, 'sat':0}}", text, flags=re.MULTILINE)
    text = re.sub(r"^base_units_list = .*$", f"base_units_list = ['{ticker}', 'm{ticker}', 'u{ticker}', 'sat']  # list(dict) does not guarantee order", text, flags=re.MULTILINE)
    text = re.sub(r"^DECIMAL_POINT_DEFAULT = .*$", f"DECIMAL_POINT_DEFAULT = 8  # {ticker} (whole coin)", text, flags=re.MULTILINE)
    text = text.replace('".electrum-blc"', f'"{coin["data_dir_unix"]}"')
    text = text.replace('"Electrum-BLC"', f'"{coin["data_dir_windows"]}"')
    # Point the block explorer at this coin's path on the Blakestream explorer.
    text = text.replace("https://explorer.blakestream.io/blc/",
                        f"https://explorer.blakestream.io/{ticker.lower()}/")
    path.write_text(text, encoding="utf-8")


def patch_bip21(workspace: Path, coin: dict) -> None:
    path = workspace / "electrum" / "bip21.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("BITCOIN_BIP21_URI_SCHEME = 'blakecoin'", f"BITCOIN_BIP21_URI_SCHEME = '{coin['uri_scheme']}'")
    text = text.replace("Not a blakecoin address", f"Not a {coin['uri_scheme']} address")
    text = text.replace("Not a blakecoin URI", f"Not a {coin['uri_scheme']} URI")
    text = text.replace("Invalid blakecoin address", f"Invalid {coin['uri_scheme']} address")
    text = text.replace(" BLC", f" {coin['ticker']}")
    path.write_text(text, encoding="utf-8")


def patch_payment_contacts_invoices(workspace: Path, coin: dict) -> None:
    payment = workspace / "electrum" / "paymentrequest.py"
    text = payment.read_text(encoding="utf-8")
    text = text.replace("application/blakecoin-paymentrequest", f"application/{coin['payment_mime_prefix']}-paymentrequest")
    text = text.replace("application/blakecoin-paymentack", f"application/{coin['payment_mime_prefix']}-paymentack")
    text = text.replace("application/blakecoin-payment", f"application/{coin['payment_mime_prefix']}-payment")
    text = text.replace("dnssec+blc", f"dnssec+{coin['openalias_prefix']}")
    text = text.replace("'User-Agent': 'Electrum'", f"'User-Agent': '{coin['app_name']}'")
    payment.write_text(text, encoding="utf-8")

    contacts = workspace / "electrum" / "contacts.py"
    text = contacts.read_text(encoding="utf-8")
    text = text.replace("prefix = 'blc'", f"prefix = '{coin['openalias_prefix']}'")
    contacts.write_text(text, encoding="utf-8")

    invoices = workspace / "electrum" / "invoices.py"
    text = invoices.read_text(encoding="utf-8")
    text = text.replace('"blakecoin:?lightning="+lightning_invoice', f'"{coin["uri_scheme"]}:?lightning="+lightning_invoice')
    invoices.write_text(text, encoding="utf-8")


def patch_desktop_and_setup(workspace: Path, coin: dict) -> None:
    (workspace / "README.md").write_text(
        f"# {coin['app_name']}\n\n"
        f"{coin['app_name']} is the Blakestream Electrum 0.25.2 wallet variant for {coin['coin_name']} ({coin['ticker']}).\n\n"
        "This package is generated from the shared Electrum source tree using the coin overlay manifest.\n"
        "It uses coin-specific address prefixes, Bech32 HRPs, URI scheme, payment MIME strings, app metadata, and icons.\n",
        encoding="utf-8",
    )

    desktop = workspace / "electrum.desktop"
    text = desktop.read_text(encoding="utf-8")
    # Generic "Bitcoin Wallet" first: doing the specific "Electrum Bitcoin Wallet" first would
    # leave "<coin> Wallet" which, for coins whose name ends in "Bitcoin" (BBTC), the generic
    # pass would then double-substitute ("BlakeBlakeBitcoin").
    text = text.replace("Bitcoin Wallet", f"{coin['coin_name']} Wallet")
    text = text.replace("Lightweight Bitcoin Client", f"Lightweight {coin['coin_name']} Client")
    text = text.replace("Electrum Bitcoin Wallet", coin["wallet_name"])
    text = text.replace("x-scheme-handler/bitcoin", f"x-scheme-handler/{coin['uri_scheme']}")
    text = text.replace("BTC", coin["ticker"])
    # Per-coin window identity so each wallet shows ITS coin icon in the dock/taskbar.
    # GNOME (Wayland) maps a window to its icon by matching the window's app_id to an
    # installed <app_id>.desktop; a shared 'electrum' id can't be told apart per coin.
    text = text.replace("StartupWMClass=electrum", f"StartupWMClass={coin['app_slug']}")
    desktop.write_text(text, encoding="utf-8")

    # The app_id itself is set in code (Qt setDesktopFileName); patch it to the slug so
    # the running window matches the installed per-coin .desktop above.
    init_py = workspace / "electrum" / "gui" / "qt" / "__init__.py"
    replace_or_fail(init_py, "QGuiApplication.setDesktopFileName('electrum')",
                    f"QGuiApplication.setDesktopFileName('{coin['app_slug']}')")

    setup_py = workspace / "setup.py"
    text = setup_py.read_text(encoding="utf-8")
    text = text.replace('name="Electrum"', f'name="{coin["app_name"]}"')
    text = text.replace('description="Lightweight Bitcoin Wallet"', f'description="Lightweight {coin["coin_name"]} Wallet"')
    text = text.replace('long_description="""Lightweight Bitcoin Wallet"""', f'long_description="""Lightweight {coin["coin_name"]} Wallet"""')
    text = text.replace('url="https://electrum.org"', 'url="https://github.com/BlueDragon747/Blakestream-Electrum"')
    setup_py.write_text(text, encoding="utf-8")

    metainfo = workspace / "org.electrum.electrum.metainfo.xml"
    replace_many(metainfo, [
        ("<name>Electrum</name>", f"<name>{coin['app_name']}</name>"),
        ("<summary>Bitcoin Wallet</summary>", f"<summary>{coin['coin_name']} Wallet</summary>"),
        (
            "Electrum is a lightweight Bitcoin wallet focused on speed, with low resource usage and simplifying Bitcoin.",
            f"{coin['app_name']} is a lightweight {coin['coin_name']} wallet focused on speed, with low resource usage and simplifying {coin['coin_name']}."
        ),
        (
            "servers that handle the most complicated parts of the Bitcoin system.",
            f"servers that handle the most complicated parts of the {coin['coin_name']} system."
        ),
        ("https://www.electrum.org/", "https://github.com/BlueDragon747/Blakestream-Electrum"),
        ("The Electrum developers", "The Blakestream Electrum developers"),
    ])

    fastlane = workspace / "fastlane" / "metadata" / "android" / "en-US"
    if fastlane.is_dir():
        replace_many(fastlane / "title.txt", [
            ("Electrum Bitcoin Wallet", coin["wallet_name"]),
        ])
        replace_many(fastlane / "short_description.txt", [
            ("Fast and self-custodial wallet for Bitcoin and the Lightning Network",
             f"Fast self-custodial wallet for {coin['coin_name']} and the Lightning Network"),
        ])
        replace_many(fastlane / "full_description.txt", [
            ("Electrum is a libre self-custodial Bitcoin wallet with support for the Lightning Network.",
             f"{coin['app_name']} is a self-custodial {coin['coin_name']} wallet with support for the Lightning Network."),
            ("trusted by the Bitcoin community since 2011.", "based on the Electrum wallet architecture."),
            ("Electrum uses servers that index the Bitcoin blockchain making it fast.",
             f"{coin['app_name']} uses servers that index the {coin['coin_name']} blockchain making it fast."),
            ("other Bitcoin clients", f"other {coin['coin_name']} clients"),
            ("Electrum Wallet verifies", f"{coin['app_name']} verifies"),
            ("https://electrum.org", "https://github.com/BlueDragon747/Blakestream-Electrum"),
            ("https://github.com/spesmilo/electrum", "https://github.com/BlueDragon747/Blakestream-Electrum"),
            ("https://crowdin.com/project/electrum", "https://github.com/BlueDragon747/Blakestream-Electrum"),
            ("electrumdev@gmail.com", "the project issue tracker"),
        ])


def patch_visible_branding(workspace: Path, coin: dict) -> None:
    coin_name = coin["coin_name"]
    unit_plural = coin["unit_plural"]
    ticker = coin["ticker"]
    app_name = coin["app_name"]
    uri_scheme = coin["uri_scheme"]
    project_url = "https://github.com/BlueDragon747/Blakestream-Electrum"

    replace_many(workspace / "electrum" / "gui" / "qt" / "main_window.py", [
        ('name = "Electrum"', f'name = "{app_name}"'),
        ("This means you will not be able to spend Bitcoins with it.",
         f"This means you will not be able to spend {unit_plural} with it."),
        ("Make sure you own the seed phrase or the private keys, before you request Bitcoins to be sent to this wallet.",
         f"Make sure you own the seed phrase or the private keys, before you request {unit_plural} to be sent to this wallet."),
        ("Testnet is separate from the main Bitcoin network. It is used for testing.",
         f"Testnet is separate from the main {coin_name} network. It is used for testing."),
        ('self.help_menu.addAction(_("&Official website"), lambda: webopen("https://electrum.org"))',
         f'self.help_menu.addAction(_("&Official website"), lambda: webopen("{project_url}"))'),
        ('self.help_menu.addAction(_("&Documentation"), lambda: webopen("http://docs.electrum.org/")).setShortcut(QKeySequence.StandardKey.HelpContents)',
         f'self.help_menu.addAction(_("&Documentation"), lambda: webopen("{project_url}")).setShortcut(QKeySequence.StandardKey.HelpContents)'),
        ('        if not constants.net.TESTNET:\n            self.help_menu.addAction(_("&Bitcoin Paper"), self.show_bitcoin_paper)\n',
         '        # Upstream whitepaper action is disabled for Blakestream coin variants.\n'),
        ("'bitcoin:%s?message=donation for %s'", f"'{uri_scheme}:%s?message=donation for %s'"),
        ('QMessageBox.about(self, "Electrum",', f'QMessageBox.about(self, "{app_name}",'),
        ("Electrum's focus is speed, with low resource usage and simplifying Bitcoin.",
         f"{app_name}'s focus is speed, with low resource usage and simplifying {coin_name}."),
        ("servers that handle the most complicated parts of the Bitcoin system.",
         f"servers that handle the most complicated parts of the {coin_name} system."),
        ("'bitcoin.pdf'", "'upstream-whitepaper.pdf'"),
        ("Fetching Bitcoin Paper...", "Fetching upstream whitepaper..."),
        ("Bitcoin- or Lightning address", f"{coin_name} or Lightning address"),
        ("Invalid Bitcoin address.", f"Invalid {coin_name} address."),
    ])

    replace_many(workspace / "electrum" / "gui" / "qt" / "history_list.py", [
        ('_("BTC balance")', f'_("{ticker} balance")'),
        ('_("BTC Fiat price")', f'_("{ticker} fiat price")'),
        ('_("BTC incoming")', f'_("{ticker} incoming")'),
        ('_("BTC outgoing")', f'_("{ticker} outgoing")'),
    ])

    replace_many(workspace / "electrum" / "gui" / "qt" / "settings_dialog.py", [
        ("1 BTC = 1000 mBTC. 1 mBTC = 1000 bits. 1 bit = 100 sat.",
         f"1 {ticker} = 1000 m{ticker}. 1 m{ticker} = 1000 u{ticker}. 1 u{ticker} = 100 sat."),
    ])

    replace_many(workspace / "electrum" / "gui" / "qt" / "wizard" / "server_connect.py", [
        ("Electrum Bitcoin Wallet", coin["wallet_name"]),
        ("Select Electrum Server", f"Select {app_name} Server"),
    ])

    replace_many(workspace / "electrum" / "gui" / "stdio.py", [
        ("Invalid Bitcoin address", f"Invalid {coin_name} address"),
    ])
    replace_many(workspace / "electrum" / "gui" / "text.py", [
        ("Invalid Bitcoin address", f"Invalid {coin_name} address"),
    ])
    replace_many(workspace / "electrum" / "contacts.py", [
        ("Invalid Bitcoin address or alias", f"Invalid {coin_name} address or alias"),
    ])
    replace_many(workspace / "electrum" / "interface.py", [
        ("Check the units, make sure you haven't confused e.g. mBTC and BTC.",
         f"Check the units, make sure you haven't confused e.g. m{ticker} and {ticker}."),
    ])
    replace_many(workspace / "electrum" / "lnaddr.py", [
        ('amount is out-of-bounds: {value!r} BTC', f'amount is out-of-bounds: {{value!r}} {ticker}'),
    ])

    replace_many(workspace / "electrum" / "commands.py", [
        ("Bitcoin address, contact or alias", f"{coin_name} address, contact or alias"),
        ("Bitcoin address", f"{coin_name} address"),
        ("Transaction fee (absolute, in BTC)", f"Transaction fee (absolute, in {ticker})"),
        ("Amount to be sent (in BTC).", f"Amount to be sent (in {ticker})."),
        ('"amount in BTC"', f'"amount in {ticker}"'),
        ("funding amount (in BTC)", f"funding amount (in {ticker})"),
        ("Push initial amount (in BTC)", f"Push initial amount (in {ticker})"),
        ("Amount (in BTC)", f"Amount (in {ticker})"),
        ("send on-chain BTC, receive on Lightning", f"send on-chain {ticker}, receive on Lightning"),
        ("Amount to be received, in BTC.", f"Amount to be received, in {ticker}."),
        ("Amount to be sent, in BTC.", f"Amount to be sent, in {ticker}."),
        ("r= part of bitcoin: URIs", f"r= part of {uri_scheme}: URIs"),
        ("https://electrum.org/", f"{project_url}/"),
    ])
    replace_many(workspace / "electrum" / "interface.py", [
        ("res.removeprefix('bitcoin:')", f"res.removeprefix('{uri_scheme}:')"),
        ("future-type\n            #       bitcoin address", f"future-type\n            #       {coin_name} address"),
    ])
    replace_many(workspace / "electrum" / "paymentrequest.py", [
        ("Guard against `bitcoin:`-URIs", f"Guard against `{uri_scheme}:`-URIs"),
    ])
    replace_many(workspace / "electrum" / "transaction.py", [
        ("Serialize the transaction as used on the Bitcoin network, into hex.",
         f"Serialize the transaction as used on the {coin_name} network, into hex."),
    ])
    replace_many(workspace / "electrum" / "submarine_swaps.py", [
        ("send on-chain BTC, receive on Lightning", f"send on-chain {ticker}, receive on Lightning"),
    ])

    replace_many(workspace / "electrum" / "gui" / "qml" / "components" / "Preferences.qml", [
        ("['BTC','mBTC','bits','sat']", f"['{ticker}','m{ticker}','u{ticker}','sat']"),
        ("Please restart Electrum to activate the new GUI settings", f"Please restart {app_name} to activate the new GUI settings"),
        ("Electrum will have to download the Lightning Network graph, which is not recommended on mobile.",
         f"{app_name} will have to download the Lightning Network graph, which is not recommended on mobile."),
    ])
    replace_many(workspace / "electrum" / "gui" / "qml" / "components" / "main.qml", [
        ("Close Electrum?", f"Close {app_name}?"),
    ])
    replace_many(workspace / "electrum" / "gui" / "qml" / "components" / "wizard" / "WCImport.qml", [
        ("Enter a list of Bitcoin addresses (this will create a watching-only wallet), or a list of private keys.",
         f"Enter a list of {coin_name} addresses (this will create a watching-only wallet), or a list of private keys."),
    ])
    replace_many(workspace / "electrum" / "gui" / "qml" / "java_classes" / "org" / "electrum" / "biometry" / "BiometricActivity.java", [
        ("Electrum Wallet", coin["wallet_name"]),
    ])

    # User-facing 'Bitcoin' strings in Qt tabs/dialogs the original pass missed.
    replace_many(workspace / "electrum" / "gui" / "qt" / "receive_tab.py", [
        ("The bitcoin address never expires and will always be part of this electrum wallet.",
         f"The {coin_name} address never expires and will always be part of this wallet."),
        ("You can reuse a bitcoin address any number of times but it is not good for your privacy.",
         f"You can reuse a {coin_name} address any number of times but it is not good for your privacy."),
        ("Bitcoin URI", f"{coin_name} URI"),
    ])
    replace_many(workspace / "electrum" / "gui" / "qt" / "request_list.py", [
        ("Bitcoin Address", f"{coin_name} Address"),
        ("Bitcoin URI", f"{coin_name} URI"),
    ])
    replace_many(workspace / "electrum" / "gui" / "qt" / "invoice_list.py", [
        ("Bitcoin Address", f"{coin_name} Address"),
    ])
    replace_many(workspace / "electrum" / "gui" / "qt" / "send_tab.py", [
        ("- a Bitcoin address or BIP21 URI", f"- a {coin_name} address or BIP21 URI"),
        ("Bitcoin Address is None", f"{coin_name} Address is None"),
    ])
    replace_many(workspace / "electrum" / "gui" / "qt" / "password_dialog.py", [
        ("Your bitcoins are password protected. However, your wallet file is not encrypted.",
         f"Your {unit_plural} are password protected. However, your wallet file is not encrypted."),
    ])
    replace_many(workspace / "electrum" / "gui" / "qt" / "confirm_tx_dialog.py", [
        ("Bitcoin transactions are in general not free. A transaction fee is paid by the sender of the funds.",
         f"{coin_name} transactions are in general not free. A transaction fee is paid by the sender of the funds."),
    ])

    # Desktop Qt wallet-creation wizard: the import-addresses page (its QML twin is already
    # rebranded above, but this Qt-desktop copy was not).
    replace_many(workspace / "electrum" / "gui" / "qt" / "wizard" / "wallet.py", [
        ("Import Bitcoin addresses or private keys", f"Import {coin_name} addresses or private keys"),
        ("Import Bitcoin Addresses or Private Keys", f"Import {coin_name} Addresses or Private Keys"),
        ("Enter a list of Bitcoin addresses (this will create a watching-only wallet), or a list of private keys.",
         f"Enter a list of {coin_name} addresses (this will create a watching-only wallet), or a list of private keys."),
    ])
    replace_many(workspace / "electrum" / "wallet.py", [
        ("Invalid bitcoin address: {}", f"Invalid {coin_name} address: {{}}"),
    ])

    # Update-check dialog/prompt: brand as Electrum-<ticker> (the check points at the
    # project's GitHub releases).
    replace_many(workspace / "electrum" / "gui" / "qt" / "main_window.py", [
        ("For security reasons we advise that you always use the latest version of Electrum.",
         f"For security reasons we advise that you always use the latest version of {app_name}."),
        ("Would you like to be notified when there is a newer version of Electrum available?",
         f"Would you like to be notified when there is a newer version of {app_name} available?"),
        ("Update to Electrum {} is available", f"Update to {app_name} {{}} is available"),
    ])
    replace_many(workspace / "electrum" / "gui" / "qt" / "update_checker.py", [
        ("'Electrum - ' + _('Update Check')", f"'{app_name} - ' + _('Update Check')"),
    ])


def patch_appimage_scripts(workspace: Path, coin: dict) -> None:
    build_sh = workspace / "contrib" / "build-linux" / "appimage" / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    text = text.replace("-t electrum-appimage-builder-img", f"-t {coin['app_slug']}-appimage-builder-img")
    text = text.replace("--name electrum-appimage-builder-cont", f"--name {coin['app_slug']}-appimage-builder-cont")
    text = text.replace("electrum-appimage-builder-img \\", f"{coin['app_slug']}-appimage-builder-img \\")
    build_sh.write_text(text, encoding="utf-8")

    make_appimage = workspace / "contrib" / "build-linux" / "appimage" / "make_appimage.sh"
    text = make_appimage.read_text(encoding="utf-8")
    text = text.replace(
        'git -C "$PROJECT_ROOT" rev-parse 2>/dev/null || fail "Building outside a git clone is not supported."',
        'git -C "$PROJECT_ROOT" rev-parse 2>/dev/null || info "building outside a git clone; using package version"',
    )
    text = text.replace(
        'VERSION=$(git describe --tags --dirty --always)',
        'VERSION=$(sed -n "s/^ELECTRUM_VERSION = \'\\\\([^\'\\\\]*\\\\)\'.*/\\\\1/p" "$PROJECT_ROOT/electrum/version.py")',
    )
    text = text.replace('APPIMAGE="$DISTDIR/electrum-$VERSION-x86_64.AppImage"', f'APPIMAGE="$DISTDIR/{coin["app_name"]}-$VERSION-x86_64.AppImage"')
    make_appimage.write_text(text, encoding="utf-8")

    make_type2_runtime = workspace / "contrib" / "build-linux" / "appimage" / "make_type2_runtime.sh"
    text = make_type2_runtime.read_text(encoding="utf-8")
    stale_pin_fix = """git apply "$CONTRIB_APPIMAGE/patches/type2-runtime-reproducible-build.patch" || fail "Failed to apply runtime repo patch"
sed -i \\
    -e 's/zlib-dev=1.3.1-r2/zlib-dev/g' \\
    -e 's/zlib-static=1.3.1-r2/zlib-static/g' \\
    -e 's/musl-dev=1.2.5-r9/musl-dev/g' \\
    scripts/docker/Dockerfile
"""
    text = text.replace(
        'git apply "$CONTRIB_APPIMAGE/patches/type2-runtime-reproducible-build.patch" || fail "Failed to apply runtime repo patch"\n',
        stale_pin_fix,
    )
    make_type2_runtime.write_text(text, encoding="utf-8")


def write_variant_manifest(workspace: Path, coin_code: str, coin: dict) -> None:
    manifest = {
        "coin": coin_code,
        "app_name": coin["app_name"],
        "network": {
            "mainnet_genesis": coin["genesis"],
            "testnet_genesis": coin["testnet_genesis"],
            "regtest_genesis": coin["regtest_genesis"],
            "segwit_hrp": coin["segwit_hrp"],
            "testnet_segwit_hrp": coin["testnet_segwit_hrp"],
            "regtest_segwit_hrp": coin["regtest_segwit_hrp"],
            "p2pkh": coin["p2pkh"],
            "p2sh": coin["p2sh"],
            "coinbase_maturity": coin["coinbase_maturity"],
        },
    }
    write_json(workspace / "VARIANT.json", manifest)


def patch_network_defaults(workspace: Path, coin: dict) -> None:
    # Fresh wallets auto-connect (auto_connect default is already True) and start on our
    # primary ElectrumX over SSL; electrum2 is the fail-over via the shipped servers.json.
    default_server = f"electrum1.blakestream.io:{coin['electrum_port_ssl']}:s"
    replace_many(workspace / "electrum" / "simple_config.py", [
        ("NETWORK_SERVER = ConfigVar('server', default=None, type_=str)",
         f"NETWORK_SERVER = ConfigVar('server', default='{default_server}', type_=str)"),
    ])


def remove_unsupported_plugins(workspace: Path) -> None:
    # TrustedCoin (two-factor authentication) is a Bitcoin-only hosted service that cannot
    # work on these chains, so the plugin is not shipped (and the wizard offers no 2FA type).
    trustedcoin = workspace / "electrum" / "plugins" / "trustedcoin"
    if trustedcoin.is_dir():
        shutil.rmtree(trustedcoin)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a coin-specific Electrum 0.25.2 wallet workspace.")
    parser.add_argument("--coin", required=True, help="coin code from coin-overlays/coins.json, e.g. PHO")
    parser.add_argument("--workspace", required=True, help="target workspace")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    coin_code = args.coin.upper()
    coin = load_coin(repo_root, coin_code)
    workspace = Path(args.workspace).resolve()

    copy_source_tree(repo_root, workspace)
    copy_overlay_assets(repo_root, workspace, coin_code, coin)
    remove_unsupported_plugins(workspace)
    clear_network_files(workspace, coin, repo_root, coin_code)
    patch_network_defaults(workspace, coin)
    patch_constants(workspace, coin)
    patch_bitcoin(workspace, coin)
    patch_util(workspace, coin)
    patch_bip21(workspace, coin)
    patch_payment_contacts_invoices(workspace, coin)
    patch_desktop_and_setup(workspace, coin)
    patch_visible_branding(workspace, coin)
    patch_appimage_scripts(workspace, coin)
    write_variant_manifest(workspace, coin_code, coin)
    print(f"Prepared {coin_code} wallet workspace at {workspace}")


if __name__ == "__main__":
    main()
