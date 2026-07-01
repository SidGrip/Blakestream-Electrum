#!/usr/bin/env bash
# Build the standalone single-coin Electrum 0.25.2 wallets — one independent
# wallet per coin — for a chosen OS. Pick a platform (interactively or as an
# arg), then it loops the coins. Everything runs on THIS machine.
#
#   build-single-wallets.sh [PLATFORM] [COIN ...]
#
# PLATFORM:  linux | windows | macos | wheel | all      (prompts if omitted)
# COIN ...:  subset of BLC BBTC ELT LIT PHO UMO          (default: all six)
#
# Where each platform builds (all on this machine):
#   linux   -> AppImage,   amd64 Docker container (any amd64 Docker host)
#   windows -> .exe,       cross-built in Docker + wine (any amd64 Docker host, incl. Linux)
#   macos   -> .dmg/.app,  native — run this on a Mac (make_osx.sh needs macOS)
#   wheel   -> sdist/wheel
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALL_COINS=(BLC BBTC ELT LIT PHO UMO)

normalize_platform() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        linux|appimage) echo linux ;;
        windows|win|wine) echo windows ;;
        macos|mac|osx|darwin) echo macos ;;
        wheel) echo wheel ;;
        all) echo all ;;
        *) return 1 ;;
    esac
}

choose_platform() {  # interactive menu -> stdout
    echo "Select the OS to build the Electrum wallets for:" >&2
    echo "  1) Linux   (AppImage)        — amd64 Docker host" >&2
    echo "  2) Windows (.exe)            — Docker + wine cross-build (any amd64 Docker host)" >&2
    echo "  3) macOS   (.dmg/.app)       — native (run on a Mac)" >&2
    echo "  4) Python wheel" >&2
    echo "  5) All (linux + windows + macos)" >&2
    local choice
    read -r -p "choice [1-5]: " choice
    case "$choice" in
        1) echo linux ;; 2) echo windows ;; 3) echo macos ;;
        4) echo wheel ;; 5) echo all ;;
        *) echo "invalid choice: $choice" >&2; return 1 ;;
    esac
}

# ---- parse args: optional platform, then optional coin list ----
PLATFORM=""
if [[ $# -ge 1 ]] && PLATFORM="$(normalize_platform "$1" 2>/dev/null)"; then
    shift
else
    PLATFORM="$(choose_platform)"
fi
COINS=("$@"); [[ ${#COINS[@]} -eq 0 ]] && COINS=("${ALL_COINS[@]}")

build_local() {  # <target> for each coin on this host
    local target="$1" coin
    local up
    for coin in "${COINS[@]}"; do
        up="$(printf '%s' "$coin" | tr '[:lower:]' '[:upper:]')"
        echo
        echo "==================== $up / $target ===================="
        "$REPO_ROOT/scripts/build_wallet_variant.sh" "$up" "$target"
    done
}

echo "== single-coin wallet build: platform=$PLATFORM coins=${COINS[*]} =="
case "$PLATFORM" in
    linux)   build_local appimage ;;
    windows) build_local windows ;;
    wheel)   build_local wheel ;;
    macos)   build_local macos ;;     # native; run on a Mac
    all)
        build_local appimage
        build_local windows
        build_local macos
        ;;
esac

echo
echo "== done: ${COINS[*]} ($PLATFORM) — artifacts under $REPO_ROOT/outputs/ =="
