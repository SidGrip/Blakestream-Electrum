#!/usr/bin/env bash
# Build the unified multiwallet desktop app (ONE BIP39 seed -> all 6 coins) as a
# self-contained platform-native desktop package. Implements the runbook in
# UNIFIED-SOURCE-OF-TRUTH.md §10.
#
#   build-multiwallet.sh [linux|windows|macos]
#
# Pipeline:
#   1. prepare 6 per-coin Electrum workspaces (each coin's constants baked in)
#   2. PyInstaller the 6 headless daemon binaries   (electrum-<coin>)
#   3. PyInstaller the supervisor binary            (electrum-backend)
#   4. stage unified/desktop/backend/{supervisor,daemons}
#   5. vite build + electron-builder for the requested platform (bundles backend/)
#   6. Linux only: bake --no-sandbox into the AppImage's AppRun
#
# Env overrides (defaults are repo-local; the live build uses /mnt/ram-build):
#   BUILD_ROOT          scratch root for venv/workspaces/backend
#                       (default: $REPO_ROOT/.build-multi)
#   ELECTRUM_BUILD_VENV  python venv dir (created if absent)
#   PYTHON_BIN          python with electrum runtime deps + pyinstaller
#   COINS               space-separated tickers (default: all six)
#   ELECTRUM_MACOS_TOOLS_DIR
#                       macOS-only repo-local tool/cache dir for Node/npm/Electron
#                       (default: $REPO_ROOT/.build-tools/macos)
#   ELECTRUM_MACOS_NODE_VERSION
#                       macOS local Node version for Electron packaging
#                       (default: 22.23.1)
#   ELECTRUM_MACOS_ALLOW_BREW_INSTALL
#                       macOS-only: install missing Homebrew native tools when set
#                       to 1 (default: 1). Set 0 to fail instead of installing.
#   ELECTRIUM_MULTI_WINE_IMAGE
#                       Windows Docker builder image used when target=windows
#                       is launched from Linux
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP="$REPO_ROOT/unified/desktop"
BUILD_ROOT="${BUILD_ROOT:-$REPO_ROOT/.build-multi}"
WSROOT="${WSROOT:-$BUILD_ROOT/wsroot}"
VENV_DIR="${ELECTRUM_BUILD_VENV:-$BUILD_ROOT/venv}"
PYTHON_BIN="${PYTHON_BIN:-}"
read -r -a COINS <<< "${COINS:-BLC BBTC ELT LIT PHO UMO}"
TARGET="${1:-linux}"

case "$(printf '%s' "$TARGET" | tr '[:upper:]' '[:lower:]')" in
    linux|appimage) TARGET="linux" ;;
    windows|win) TARGET="windows" ;;
    macos|mac|osx|darwin) TARGET="macos" ;;
    *) echo "unknown target: $TARGET" >&2; exit 1 ;;
esac

HOST_OS="$(uname -s 2>/dev/null || printf unknown)"
case "$HOST_OS" in
    MINGW*|MSYS*|CYGWIN*) HOST_FAMILY="windows"; EXE=".exe"; ADD_BINARY_SEP=";" ;;
    Darwin)              HOST_FAMILY="macos";   EXE="";     ADD_BINARY_SEP=":" ;;
    *)                   HOST_FAMILY="linux";   EXE="";     ADD_BINARY_SEP=":" ;;
esac

if [ "$TARGET" = "windows" ] && [ "$HOST_FAMILY" = "linux" ]; then
    exec "$REPO_ROOT/scripts/build-multiwallet-windows-docker.sh"
fi

if [ "$TARGET" != "$HOST_FAMILY" ]; then
    echo "target $TARGET must be built on $TARGET (current host is $HOST_FAMILY)" >&2
    exit 2
fi

if [ "$TARGET" = "macos" ]; then
    # Source so PATH/npm/Electron cache exports apply to this build process.
    . "$REPO_ROOT/scripts/bootstrap-macos-build-env.sh"
fi

venv_python_path() {
    case "$HOST_FAMILY" in
        windows) printf '%s\n' "$VENV_DIR/Scripts/python.exe" ;;
        *)       printf '%s\n' "$VENV_DIR/bin/python" ;;
    esac
}

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(venv_python_path)"
fi

native_lib=""

electrum_ecc_package_dir() {
    "$PYTHON_BIN" - <<'PY'
import importlib.util

spec = importlib.util.find_spec("electrum_ecc")
if spec is None or spec.submodule_search_locations is None:
    raise SystemExit("electrum_ecc package not found")
print(spec.submodule_search_locations[0])
PY
}

ensure_macos_electrum_ecc_secp() {
    [ "$TARGET" = "macos" ] || return 0

    local pkg_dir cached_secp dll_dir
    pkg_dir="$(electrum_ecc_package_dir)"
    if ls "$pkg_dir"/libsecp256k1*.dylib >/dev/null 2>&1; then
        return 0
    fi

    cached_secp="$(
        find \
            "$REPO_ROOT/build/workspaces" \
            "$REPO_ROOT/contrib/osx/.cache/dlls" \
            "$REPO_ROOT/contrib/secp256k1/dist/lib" \
            "$HOME/Library/Application Support/pyinstaller" \
            -name 'libsecp256k1*.dylib' 2>/dev/null | head -1 || true
    )"

    if [ -z "$cached_secp" ]; then
        command -v autoreconf >/dev/null 2>&1 || {
            echo "macOS libsecp256k1 dylib not found and autoreconf is unavailable; install autoconf/automake/libtool" >&2
            exit 1
        }
        dll_dir="$BUILD_ROOT/dlls"
        mkdir -p "$dll_dir"
        ( cd "$REPO_ROOT" && DLL_TARGET_DIR="$dll_dir" "$REPO_ROOT/contrib/make_libsecp256k1.sh" )
        cached_secp="$(find "$dll_dir" -name 'libsecp256k1*.dylib' 2>/dev/null | head -1 || true)"
    fi

    [ -n "$cached_secp" ] && [ -f "$cached_secp" ] || {
        echo "macOS libsecp256k1 dylib could not be prepared" >&2
        exit 1
    }
    cp -f "$cached_secp" "$pkg_dir/"
}

ensure_blake256_native() {
    case "$TARGET" in
        windows)
            native_lib="$(ls "$REPO_ROOT"/blake256.dll 2>/dev/null | head -1 || true)"
            if [ -z "$native_lib" ]; then
                command -v gcc >/dev/null 2>&1 || {
                    echo "gcc is required to build blake256.dll on Windows" >&2
                    exit 1
                }
                gcc -O2 -shared -I"$REPO_ROOT/blake256" \
                    -o "$REPO_ROOT/blake256.dll" \
                    "$REPO_ROOT/blake256/blake256_dll.c" \
                    "$REPO_ROOT/blake256/blake.c"
                native_lib="$REPO_ROOT/blake256.dll"
            fi
            ;;
        *)
            native_lib="$(ls "$REPO_ROOT"/_blake256*.so 2>/dev/null | head -1 || true)"
            if [ -z "$native_lib" ]; then
                ( cd "$REPO_ROOT" && "$PYTHON_BIN" setup.py build_ext --inplace )
                native_lib="$(ls "$REPO_ROOT"/_blake256*.so 2>/dev/null | head -1 || true)"
            fi
            ;;
    esac
    [ -n "$native_lib" ] && [ -f "$native_lib" ] || {
        echo "blake256 native library not found for $TARGET" >&2
        exit 1
    }
}

ensure_python_env() {
    if [ ! -x "$PYTHON_BIN" ]; then
        echo "== creating build venv at $VENV_DIR =="
        python3 -m venv "$VENV_DIR"
        PYTHON_BIN="$(venv_python_path)"
    fi
    "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel pyinstaller
    if [ "$TARGET" = "macos" ] && ! command -v autoreconf >/dev/null 2>&1; then
        export ELECTRUM_ECC_DONT_COMPILE=1
    fi
    "$PYTHON_BIN" -m pip install -r "$REPO_ROOT/contrib/requirements/requirements.txt"
    "$PYTHON_BIN" -m pip install -r "$REPO_ROOT/contrib/requirements/requirements-hw.txt"
    "$PYTHON_BIN" -m pip install 'cryptography==48.0.1' 'pycryptodomex==3.23.0' 'argon2-cffi==25.1.0'
    "$PYTHON_BIN" -m pip install "$REPO_ROOT/blake256"
    ensure_macos_electrum_ecc_secp
}

# 1. per-coin workspaces (full Electrum source trees with that coin's net)
prepare_workspaces() {
    mkdir -p "$WSROOT"
    for C in "${COINS[@]}"; do
        echo "== workspace: $C =="
        "$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_wallet_variant.py" \
            --coin "$C" --workspace "$WSROOT/$C"
    done
}

# 2. the six headless daemon binaries (electrum-<coin>)
build_daemons() {
    rm -rf "$BUILD_ROOT/daemons-out"
    PYTHON_BIN="$PYTHON_BIN" "$DESKTOP/build-backend.sh" \
        "$WSROOT" "$BUILD_ROOT/daemons-out" "$native_lib" "${COINS[@]}"
}

# 3. the supervisor binary (electrum-backend = python -m unified.launcher).
#    PYTHONPATH=<repo> is REQUIRED so collect-submodules('unified') bundles the
#    live package, not stale/empty code (see SoT §10 footgun).
build_supervisor() {
    local out="$BUILD_ROOT/supervisor-out"
    rm -rf "$out"
    ( cd "$BUILD_ROOT" && PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m PyInstaller \
        --onedir --noconfirm --clean \
        --name electrum-backend \
        --distpath "$out/dist" --workpath "$out/build" --specpath "$out/spec" \
        --collect-all electrum --collect-all electrum_ecc \
        --collect-submodules unified \
        --collect-all argon2 \
        --exclude-module PyQt5 --exclude-module PyQt6 \
        --add-data "$REPO_ROOT/coin-overlays${ADD_BINARY_SEP}coin-overlays" \
        --add-binary "$native_lib$ADD_BINARY_SEP." \
        --paths "$REPO_ROOT" \
        "$REPO_ROOT/unified/launcher.py" )
    [ -x "$out/dist/electrum-backend/electrum-backend$EXE" ] \
        || { echo "supervisor build FAILED" >&2; exit 1; }
}

# 4. stage the self-contained backend electron-builder bundles as extraResources
stage_backend() {
    local b="$DESKTOP/backend"
    rm -rf "$b"
    mkdir -p "$b/supervisor" "$b/daemons"
    cp -a "$BUILD_ROOT/supervisor-out/dist/electrum-backend" "$b/supervisor/"
    for C in "${COINS[@]}"; do
        local lc; lc="$(echo "$C" | tr 'A-Z' 'a-z')"
        cp -a "$BUILD_ROOT/daemons-out/dist/electrum-$lc" "$b/daemons/"
    done
}

# 5. renderer + Electron package
build_desktop() {
    case "$TARGET" in
        linux)   builder_args=(--linux AppImage) ;;
        windows) builder_args=(--win portable nsis --x64) ;;
        macos)   builder_args=(--mac dmg zip --x64) ;;
    esac
    ( cd "$DESKTOP" \
        && rm -rf release \
        && if [ -f package-lock.json ]; then npm ci; else npm install; fi \
        && npm run build \
        && npx electron-builder "${builder_args[@]}" --config.directories.output=release )
}

# 6. make a plain double-click work on modern Ubuntu
bake_sandbox() {
    local img
    img="$(ls -t "$DESKTOP"/release/*.AppImage 2>/dev/null | head -1 || true)"
    [ -n "$img" ] || { echo "no AppImage produced under $DESKTOP/release" >&2; exit 1; }
    "$DESKTOP/fix-appimage-sandbox.sh" "$img"
    echo "== built: $img =="
}

stage_outputs() {
    # The multiwallet has its OWN output dir; outputs/hub/ belongs to the Hub build and
    # must not be wiped here (it holds the Hub AppImage release artifact).
    local outdir="$REPO_ROOT/outputs/multiwallet/$TARGET"
    rm -rf "$outdir"
    mkdir -p "$outdir"
    find "$DESKTOP/release" -maxdepth 1 -type f \
        \( -name '*.AppImage' -o -name '*.dmg' -o -name '*.zip' -o -name '*.exe' -o -name '*.blockmap' -o -name '*.yml' \) \
        -exec cp -f {} "$outdir/" \;
    if [ "$TARGET" = "windows" ] && [ -d "$DESKTOP/release/win-unpacked" ]; then
        cp -a "$DESKTOP/release/win-unpacked" "$outdir/"
    fi
    find "$outdir" -maxdepth 1 -type f -print | sort
}

ensure_python_env
ensure_blake256_native
prepare_workspaces
build_daemons
build_supervisor
stage_backend
build_desktop
[ "$TARGET" = "linux" ] && bake_sandbox
stage_outputs
echo "== multiwallet build complete: $TARGET =="
