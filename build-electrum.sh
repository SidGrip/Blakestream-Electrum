#!/usr/bin/env bash
# User-facing Electrium builder for this cloned multicoin repo.
#
# Examples:
#   ./build-electrum.sh -blc -pho --linux
#   ./build-electrum.sh -all --linux --windows
#   ./build-electrum.sh -multi --windows
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALL_COINS=(BLC BBTC ELT LIT PHO UMO)
COINS=()
TARGETS=()
COIN_COUNT=0
TARGET_COUNT=0
BUILD_MULTI=0
DRY_RUN=0

usage() {
    cat <<'EOF'
usage: ./build-electrum.sh [products] [targets] [options]

Products, choose at least one:
  -blc -bbtc -elt -lit -pho -umo   build selected standalone coin wallets
  -all                             build all standalone coin wallets
  -multi                           build the unified one-seed multiwallet

Targets:
  --linux | -linux                 Linux AppImage
  --windows | -windows             Windows artifacts
  --macos | -macos                 macOS .dmg/.app or .zip
  --wheel | -wheel                 Python sdist/wheel for standalone coin wallets only

If no target is given:
  Linux x86_64 Docker host -> linux + windows
  macOS host               -> macos

Options:
  --dry-run                        print build commands without running them
  -h | --help                      show this help

Examples:
  ./build-electrum.sh -blc
  ./build-electrum.sh -blc -pho --linux
  ./build-electrum.sh -all --linux --windows
  ./build-electrum.sh -multi --linux
  ./build-electrum.sh -multi --windows
  ./build-electrum.sh -all -multi --linux --windows
  ./build-electrum.sh -all --macos
  ./build-electrum.sh -multi --macos
EOF
}

die() { echo "error: $*" >&2; exit 1; }

have_docker() { command -v docker >/dev/null 2>&1; }
is_amd64() {
    case "$(uname -m 2>/dev/null || printf unknown)" in
        x86_64|amd64) return 0 ;;
        *) return 1 ;;
    esac
}

add_coin() {
    local coin="$1" existing
    if [ "$COIN_COUNT" -gt 0 ]; then
        for existing in "${COINS[@]}"; do
            [ "$existing" = "$coin" ] && return 0
        done
    fi
    COINS+=("$coin")
    COIN_COUNT=$((COIN_COUNT + 1))
}

add_target() {
    local target="$1" existing
    if [ "$TARGET_COUNT" -gt 0 ]; then
        for existing in "${TARGETS[@]}"; do
            [ "$existing" = "$target" ] && return 0
        done
    fi
    TARGETS+=("$target")
    TARGET_COUNT=$((TARGET_COUNT + 1))
}

add_all_coins() {
    local coin
    for coin in "${ALL_COINS[@]}"; do add_coin "$coin"; done
}

normalize_arg() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

while [ $# -gt 0 ]; do
    arg="$(normalize_arg "$1")"
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --dry-run|-dry-run) DRY_RUN=1 ;;

        -blc|--blc) add_coin BLC ;;
        -bbtc|--bbtc) add_coin BBTC ;;
        -elt|--elt) add_coin ELT ;;
        -lit|--lit) add_coin LIT ;;
        -pho|--pho) add_coin PHO ;;
        -umo|--umo) add_coin UMO ;;
        -all|--all) add_all_coins ;;
        -multi|--multi) BUILD_MULTI=1 ;;

        --linux|-linux|--appimage|-appimage) add_target linux ;;
        --windows|-windows|--win|-win) add_target windows ;;
        --macos|-macos|--mac|-mac|--osx|-osx) add_target macos ;;
        --wheel|-wheel) add_target wheel ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

if [ "$COIN_COUNT" -eq 0 ] && [ "$BUILD_MULTI" -eq 0 ]; then
    usage >&2
    die "choose at least one product: a ticker, -all, or -multi"
fi

if [ "$TARGET_COUNT" -eq 0 ]; then
    case "$(uname -s 2>/dev/null || printf unknown)" in
        Darwin) add_target macos ;;
        Linux)
            if is_amd64 && have_docker; then
                add_target linux
                add_target windows
            else
                die "no default target for this Linux host; install Docker on x86_64 or pass an explicit target"
            fi
            ;;
        *) die "no default target for this host; pass --linux, --windows, --macos, or --wheel" ;;
    esac
fi

for target in "${TARGETS[@]}"; do
    if [ "$target" = "wheel" ] && [ "$BUILD_MULTI" -eq 1 ]; then
        die "--wheel applies to standalone coin wallets only; remove -multi or remove --wheel"
    fi
done

run_cmd() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    [ "$DRY_RUN" -eq 1 ] || "$@"
}

target_for_single() {
    case "$1" in
        linux) echo appimage ;;
        windows) echo windows ;;
        macos) echo macos ;;
        wheel) echo wheel ;;
    esac
}

write_sha256sums() {
    local dir="$1"
    [ -d "$dir" ] || return 0
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "+ (cd $(printf '%q' "$dir") && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS)"
        return 0
    fi
    (
        cd "$dir"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS
    )
}

summarize_dir() {
    local dir="$1"
    [ -d "$dir" ] || return 0
    echo
    echo "== artifacts: $dir =="
    find "$dir" -maxdepth 2 \( -type f -o -type d -name '*.app' \) \
        ! -name '.DS_Store' -print | sort
}

echo "== Electrium build =="
coin_list="(none)"
[ "$COIN_COUNT" -gt 0 ] && coin_list="${COINS[*]}"
echo "products: singles=$coin_list multi=$BUILD_MULTI"
echo "targets: ${TARGETS[*]}"
[ "$DRY_RUN" -eq 1 ] && echo "mode: dry-run"

for target in "${TARGETS[@]}"; do
    single_target="$(target_for_single "$target")"
    if [ "$COIN_COUNT" -gt 0 ]; then
        for coin in "${COINS[@]}"; do
            echo
            echo "==================== standalone $coin / $target ===================="
            run_cmd "$REPO_ROOT/scripts/build_wallet_variant.sh" "$coin" "$single_target"
            out_kind="$target"
            [ "$target" = "linux" ] && out_kind="linux"
            [ "$target" = "wheel" ] && out_kind="python"
            write_sha256sums "$REPO_ROOT/outputs/$coin/$out_kind"
        done
    fi

    if [ "$BUILD_MULTI" -eq 1 ]; then
        echo
        echo "==================== multiwallet / $target ===================="
        run_cmd "$REPO_ROOT/scripts/build-multiwallet.sh" "$target"
        write_sha256sums "$REPO_ROOT/outputs/multiwallet/$target"
    fi
done

echo
echo "== build summary =="
if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: artifact summary skipped"
else
    if [ "$COIN_COUNT" -gt 0 ]; then
        for coin in "${COINS[@]}"; do
            summarize_dir "$REPO_ROOT/outputs/$coin"
        done
    fi
    [ "$BUILD_MULTI" -eq 1 ] && summarize_dir "$REPO_ROOT/outputs/multiwallet"
fi

echo
echo "== done =="
