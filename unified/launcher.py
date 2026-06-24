"""Unified launcher — TWO products on ONE engine.

Blakestream ships two Electrum products that share the same per-coin Electrum
daemons, ``coins.json`` metadata, and provisioning library. Only the seed model
differs:

  * **single <COIN>** — a standalone single-coin wallet with its OWN seed (the
    classic Electrum experience).
  * **multi** — the multiwallet Electrum: ONE seed deriving all six coins, with a
    unified portfolio.

Both drive the headless per-coin daemons via :class:`unified.orchestrator.Orchestrator`.

Seed handling is deliberately argv-safe: a restore seed is read from **stdin**
(``--restore``), never passed on the command line; ``--create`` generates a fresh
one. (A real desktop build sources the seed from the encrypted vault / a prompt;
this CLI is the headless backend + smoke harness.)

Examples (on the build server)::

    # multiwallet, restore the shared seed from stdin, bring up all six, then stop
    echo "<12 words>" | ELECTRUM_WSROOT=/mnt/ram-build/wsroot \\
        python -m unified.launcher --datadirs /mnt/ram-build/odd --smoke multi --restore

    # single-coin wallet with its own fresh seed
    ELECTRUM_WSROOT=/mnt/ram-build/wsroot \\
        python -m unified.launcher --datadirs /mnt/ram-build/odd --smoke single BLC --create
"""

import argparse
import os
import secrets
import sys
import threading

from unified import provisioning, vault
from unified.orchestrator import DEFAULT_RPC_PORTS, Orchestrator, DAEMON_DEFAULT

# Every coin is served by electrum1/electrum2; each daemon's bundled servers.json +
# NETWORK_SERVER default bring it online and auto-connect. Per coin, ELECTRUM_SERVER_<TICKER>
# overrides with an explicit "host:port:s" — or an empty string to force that coin offline.


def _exe_name(name: str) -> str:
    return name + ".exe" if sys.platform == "win32" else name


def _servers(tickers):
    out = {}
    for t in tickers:
        env = os.environ.get(f"ELECTRUM_SERVER_{t}")
        if env is None:
            out[t] = DAEMON_DEFAULT          # use the daemon's own baked default (online)
        else:
            out[t] = env or None             # explicit server, or "" => force offline
    return out


def _orchestrator(tickers, args) -> Orchestrator:
    binaries = None
    if args.backend_dir:  # packaged: standalone per-coin daemon binaries
        binaries = {t: os.path.join(args.backend_dir, f"electrum-{t.lower()}",
                                    _exe_name(f"electrum-{t.lower()}")) for t in tickers}
    return Orchestrator(
        python_bin=args.python,
        workspaces_root=args.workspaces or "",
        datadirs_root=args.datadirs,
        servers=_servers(tickers),
        binaries=binaries,
    )


def _vault_password():
    pw = os.environ.get("ELECTRUM_VAULT_PASSWORD")
    if pw is not None:
        return pw
    if not sys.stdin.isatty():
        return None   # headless (e.g. spawned by the app): don't block; the UI unlocks via the API
    import getpass
    return getpass.getpass("Vault password: ")


def _acquire_mnemonic(args, *, shared: bool):
    # Existing vault, no create/restore -> unlock and source the seed from it.
    if args.vault and vault.vault_exists(args.vault) and not (args.create or args.restore):
        pw = _vault_password()
        if pw is None:
            return None   # start unprovisioned; the UI unlocks the vault via the API
        return vault.unlock_vault(args.vault, pw)

    # Never silently clobber an existing vault on a CLI --create/--restore.
    if (args.create or args.restore) and args.vault and vault.vault_exists(args.vault) \
            and not getattr(args, "force", False):
        sys.exit(f"vault already exists at {args.vault}; omit --create/--restore to unlock it, "
                 f"or pass --force to overwrite it")

    mnemonic = None
    if args.create:
        mnemonic = provisioning.generate_mnemonic()
        scope = "ONE shared seed for all six coins" if shared else "this coin's own seed"
        print(f"[seed] generated {scope} ({len(mnemonic.split())} words) — WRITE IT DOWN:\n  {mnemonic}")
    elif args.restore:
        mnemonic = sys.stdin.readline().strip()
        if not provisioning.is_valid_bip39(mnemonic):
            sys.exit("invalid BIP39 mnemonic on stdin")

    # Seal a freshly created/restored seed into the vault (encrypted at rest). Sealing
    # needs a real password — a headless shell (no TTY / no env var) can't be prompted.
    if mnemonic is not None and args.vault:
        pw = _vault_password()
        if pw is None:
            sys.exit("--create/--restore with --vault needs a password "
                     "(set ELECTRUM_VAULT_PASSWORD or run on a TTY)")
        vault.create_vault(args.vault, mnemonic, pw)
        print(f"[vault] sealed seed at {args.vault}")
    return mnemonic  # None -> bring up with no provisioning (existing/UI-created wallet)


def cmd_single(args):
    ticker = args.coin.upper()
    orch = _orchestrator([ticker], args)
    mnemonic = _acquire_mnemonic(args, shared=False)
    if args.serve:
        def _bg():
            orch.status[ticker] = "starting"
            try:
                orch.bring_up(ticker, mnemonic)
                orch.status[ticker] = "ready"
            except Exception:
                orch.status[ticker] = "failed"
            finally:
                orch._supervision_enabled = True   # bring-up done -> supervisor may act
        threading.Thread(target=_bg, daemon=True).start()
        return orch
    orch.bring_up(ticker, mnemonic)
    try:
        addr = orch.first_address(ticker)
    except Exception:
        addr = "(locked / no wallet yet)"
    print(f"[single {ticker}] ready on 127.0.0.1:{orch.daemons[ticker].rpc_port}  first_address={addr}")
    return orch


def cmd_multi(args):
    tickers = list(DEFAULT_RPC_PORTS)
    orch = _orchestrator(tickers, args)
    mnemonic = _acquire_mnemonic(args, shared=True)
    if args.serve:
        # Serve-first: bring the six daemons up in the BACKGROUND so the API (and the
        # /startup progress the Connecting screen polls) is live immediately and the UI
        # can light up each coin's icon as its daemon becomes ready.
        threading.Thread(target=lambda: orch.bring_up_all(mnemonic), daemon=True).start()
        return orch
    errors = orch.bring_up_all(mnemonic)
    if errors:
        print(f"[multi] some coins degraded (still serving the rest): {errors}")
    print("[multi] portfolio:")
    for ticker in tickers:
        mode = "online" if orch.is_online(ticker) else "offline"
        try:
            addr = orch.first_address(ticker)
        except Exception:
            addr = "-"   # not loaded yet (e.g. relaunch awaiting unlock)
        print(f"  {ticker:4} :{orch.daemons[ticker].rpc_port} [{mode:7}] {addr}")
    return orch


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--python", default=os.environ.get("ELECTRUM_PYBIN", sys.executable))
    common.add_argument("--workspaces", default=os.environ.get("ELECTRUM_WSROOT"),
                        help="root of the generated per-coin workspaces (dev/source mode)")
    common.add_argument("--backend-dir", default=os.environ.get("ELECTRUM_BACKEND_DIR"),
                        help="dir of bundled per-coin daemon binaries (packaged mode)")
    common.add_argument("--datadirs", default=os.environ.get(
        "ELECTRUM_DDROOT", os.path.expanduser("~/.blakestream/electrum")))
    common.add_argument("--create", action="store_true", help="generate a fresh seed")
    common.add_argument("--restore", action="store_true", help="restore a seed read from STDIN")
    common.add_argument("--vault", default=os.environ.get("ELECTRUM_VAULT"),
                        help="encrypted seed vault path (unlock it, or seal a new/restored seed)")
    common.add_argument("--force", action="store_true",
                        help="overwrite an existing vault when --create/--restore is given")
    common.add_argument("--smoke", action="store_true", help="stop the daemon(s) after bring-up (testing)")
    common.add_argument("--serve", action="store_true",
                        help="after bring-up, run the loopback HTTP API (blocks until interrupted)")
    common.add_argument("--api-port", type=int, default=57100, help="loopback API port for --serve")

    p = argparse.ArgumentParser(prog="unified.launcher", parents=[common],
                                description="Blakestream Electrum launcher (single-wallet or multiwallet)")
    sub = p.add_subparsers(dest="mode", required=True)
    sp = sub.add_parser("single", parents=[common], help="standalone single-coin wallet (own seed)")
    sp.add_argument("coin")
    sp.set_defaults(func=cmd_single)
    sub.add_parser("multi", parents=[common], help="multiwallet: one seed -> all six coins").set_defaults(func=cmd_multi)

    args = p.parse_args(argv)
    if not args.workspaces and not args.backend_dir:
        p.error("set --workspaces (dev) or --backend-dir (packaged)")
    if args.create and args.restore:
        p.error("--create and --restore are mutually exclusive")
    if args.smoke and args.serve:
        p.error("--smoke and --serve are mutually exclusive")

    orch = args.func(args)
    if args.smoke:
        orch.stop_all()
        print("(smoke) stopped")
    elif args.serve:
        import signal
        from unified.api import serve

        # Defence-in-depth: stop this supervisor (which holds the seed-derived session keys and,
        # briefly, the vault password during Argon2) from being core-dumped or ptraced by a non-root
        # local process, so a crash/hibernation can't spill secrets to disk. Linux-only, best-effort.
        # NOTE: load libc explicitly — in the PyInstaller-frozen bundle ``CDLL(None)`` resolves the
        # bootloader's symbols, which do NOT expose prctl, so the call would silently no-op.
        try:
            import ctypes
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            libc.prctl(4, 0, 0, 0, 0)                   # PR_SET_DUMPABLE = 4, value 0
        except Exception:
            pass

        def _term(*_a):  # stop the daemons cleanly when the app kills us
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, _term)

        vault_path = args.vault or os.path.join(args.datadirs, "vault.enc")
        # Fail closed: the served API must NEVER be tokenless (that would expose
        # seed-minting/send/unlock to anything on loopback). The packaged app always
        # passes ELECTRUM_API_TOKEN; if a manual launch omits it, mint one so the API
        # still requires a bearer token (the env-provided token isn't printed/logged).
        token = os.environ.get("ELECTRUM_API_TOKEN") or secrets.token_urlsafe(32)

        # Supervision: a background pass restarts any daemon whose RPC has gone away
        # (ensure_running applies a per-coin exponential back-off so a flapping daemon
        # can't spin). Without this a crashed coin stays down for the whole session.
        import time as _time

        def _supervise():
            while not orch._stopping:
                _time.sleep(5)
                if orch._stopping:        # stop_all() began: don't resurrect torn-down daemons
                    return
                try:
                    orch.supervise_once()
                except Exception:
                    pass

        threading.Thread(target=_supervise, daemon=True).start()

        print(f"[serve] API on http://127.0.0.1:{args.api_port} (Ctrl-C to stop)")
        try:
            serve(orch, port=args.api_port, vault_path=vault_path, token=token)
        except KeyboardInterrupt:
            pass
        finally:
            orch.stop_all()


if __name__ == "__main__":
    main()
