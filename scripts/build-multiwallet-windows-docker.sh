#!/usr/bin/env bash
# Cross-build the unified multiwallet Windows package from a Linux Docker host.
#
# This uses the existing Electrium Wine base and adds Node/npm for electron-builder.
# Push is intentionally opt-in: pass --push-image or set PUSH_IMAGE=1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${ELECTRIUM_MULTI_WINE_IMAGE:-sidgrip/electrium-multiwallet-wine-builder:25.2}"
PUSH_IMAGE="${PUSH_IMAGE:-0}"
INSIDE=0

usage() {
    cat <<EOF
usage: scripts/build-multiwallet-windows-docker.sh [--image IMAGE] [--push-image]

Environment:
  BUILD_ROOT                  scratch dir inside the repo mount (default: .build-multi/windows)
  COINS                       space-separated tickers (default: all six)
  ELECTRIUM_MULTI_WINE_IMAGE  docker image tag (default: $IMAGE)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --inside) INSIDE=1 ;;
        --image) shift; IMAGE="${1:?missing image}" ;;
        --push-image) PUSH_IMAGE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

if [ "$INSIDE" -eq 0 ]; then
    [ "$(uname -s 2>/dev/null || printf unknown)" = "Linux" ] || {
        echo "Windows Docker build must be launched from Linux" >&2
        exit 2
    }
    command -v docker >/dev/null 2>&1 || {
        echo "Docker is required for the Windows multiwallet cross-build" >&2
        exit 2
    }

    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    cat > "$tmp/Dockerfile" <<'DOCKER'
FROM sidgrip/electrum-wine-base:25.2
USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -q && apt-get install -qy --no-install-recommends \
        nodejs npm zip xz-utils python3-venv \
    && rm -rf /var/lib/apt/lists/*
ARG UID=1000
RUN if ! getent passwd "$UID" >/dev/null; then \
        useradd --uid "$UID" --create-home --shell /bin/bash user; \
    fi \
    && usermod --append --groups sudo "$(getent passwd "$UID" | cut -d: -f1)" \
    && echo "%sudo ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers \
    && chown -R "$UID" /opt /home || true
WORKDIR /opt/wine64/drive_c/electrum
USER $UID
DOCKER
    echo "== building/pulling Windows multiwallet builder image: $IMAGE =="
    docker build --build-arg UID="$(id -u)" -t "$IMAGE" "$tmp"
    if [ "$PUSH_IMAGE" = "1" ]; then
        docker push "$IMAGE"
    fi

    echo "== running Windows multiwallet build in Docker =="
    docker run --rm \
        -v "$REPO_ROOT:/opt/wine64/drive_c/electrum" \
        -w /opt/wine64/drive_c/electrum \
        -e BUILD_ROOT="${BUILD_ROOT:-/opt/wine64/drive_c/electrum/.build-multi/windows}" \
        -e COINS="${COINS:-BLC BBTC ELT LIT PHO UMO}" \
        "$IMAGE" \
        bash scripts/build-multiwallet-windows-docker.sh --inside
    exit 0
fi

DESKTOP="$REPO_ROOT/unified/desktop"
BUILD_ROOT="${BUILD_ROOT:-$REPO_ROOT/.build-multi/windows}"
WSROOT="${WSROOT:-$BUILD_ROOT/wsroot}"
read -r -a COINS_ARR <<< "${COINS:-BLC BBTC ELT LIT PHO UMO}"
WINE_CONTRIB="$REPO_ROOT/contrib/build-wine"

export WIN_ARCH="${WIN_ARCH:-win64}"
export GCC_TRIPLET_HOST="${GCC_TRIPLET_HOST:-x86_64-w64-mingw32}"
export BUILD_TYPE="wine"
export GCC_TRIPLET_BUILD="${GCC_TRIPLET_BUILD:-x86_64-pc-linux-gnu}"
export GCC_STRIP_BINARIES="${GCC_STRIP_BINARIES:-1}"
export CONTRIB="$REPO_ROOT/contrib"
export PROJECT_ROOT="$REPO_ROOT"
export CACHEDIR="$WINE_CONTRIB/.cache/$WIN_ARCH/build"
export PIP_CACHE_DIR="$WINE_CONTRIB/.cache/$WIN_ARCH/wine_pip_cache"
export WINE_PIP_CACHE_DIR="c:/electrum/contrib/build-wine/.cache/$WIN_ARCH/wine_pip_cache"
export DLL_TARGET_DIR="$CACHEDIR/dlls"
export WINEPREFIX="${WINEPREFIX:-/opt/wine64}"
export WINEDEBUG="${WINEDEBUG:--all}"
export WINE_PYHOME="${WINE_PYHOME:-c:/python3}"
export WINE_PYTHON="${WINE_PYTHON:-wine $WINE_PYHOME/python.exe -B}"

wine_python() {
    wine "$WINE_PYHOME/python.exe" -B "$@"
}

win_path() {
    local path="$1"
    case "$path" in
        "$REPO_ROOT") printf 'c:/electrum\n' ;;
        "$REPO_ROOT"/*) printf 'c:/electrum%s\n' "${path#$REPO_ROOT}" ;;
        *) winepath -w "$path" | tr '\\' '/' ;;
    esac
}

ensure_wine_prefix_owner() {
    local uid gid owner
    uid="$(id -u)"
    gid="$(id -g)"
    owner="$(stat -c '%u' "$WINEPREFIX" 2>/dev/null || printf unknown)"
    if [ "$owner" != "$uid" ]; then
        echo "== fixing Wine prefix ownership: $WINEPREFIX =="
        sudo chown -R "$uid:$gid" "$WINEPREFIX"
    fi
}

ensure_wine_python() {
    ensure_wine_prefix_owner
    mkdir -p "$CACHEDIR" "$DLL_TARGET_DIR" "$PIP_CACHE_DIR" "$BUILD_ROOT"
    if ! ls "$DLL_TARGET_DIR"/libsecp256k1-*.dll >/dev/null 2>&1; then
        "$CONTRIB/make_libsecp256k1.sh"
    fi
    if [ ! -f "$DLL_TARGET_DIR/libzbar-0.dll" ]; then
        (
            WIN_ICONV_COMMIT="9f98392dfecadffd62572e73e9aba878e03496c4"
            cd "$CACHEDIR"
            if [ ! -d win-iconv ]; then git clone https://github.com/win-iconv/win-iconv.git; fi
            cd win-iconv
            if ! git cat-file -e "${WIN_ICONV_COMMIT}^{commit}" 2>/dev/null; then git fetch --all; fi
            git reset --hard
            git clean -dfxq
            git checkout "${WIN_ICONV_COMMIT}^{commit}"
            CC="${GCC_TRIPLET_HOST}-gcc" make -j1
            sudo make install prefix="/usr/${GCC_TRIPLET_HOST}"
        )
        "$CONTRIB/make_zbar.sh"
    fi
    if [ ! -f "$DLL_TARGET_DIR/libusb-1.0.dll" ]; then
        "$CONTRIB/make_libusb.sh"
    fi
    "$WINE_CONTRIB/prepare-wine.sh"
}

ensure_wine_deps() {
    local requirements_win blake256_win
    requirements_win="$(win_path "$REPO_ROOT/contrib/requirements/requirements.txt")"
    blake256_win="$(win_path "$REPO_ROOT/blake256")"
    wine_python -m pip install --upgrade pip setuptools wheel pyinstaller
    wine_python -m pip install -r "$requirements_win"
    wine_python -m pip install "cryptography==45.0.3" "pycryptodomex>=3.7" "argon2-cffi>=21.3"
    wine_python -m pip install "$blake256_win"
    cp -f "$DLL_TARGET_DIR"/libsecp256k1-*.dll "$WINEPREFIX/drive_c/python3/Lib/site-packages/electrum_ecc/"
}

build_blake256_dll() {
    local out="$BUILD_ROOT/blake256.dll"
    if [ ! -f "$out" ]; then
        "${GCC_TRIPLET_HOST}-gcc" -O2 -shared -I"$REPO_ROOT/blake256" \
            -o "$out" \
            "$REPO_ROOT/blake256/blake256_dll.c" \
            "$REPO_ROOT/blake256/blake.c"
    fi
    printf '%s\n' "$out"
}

prepare_workspaces() {
    mkdir -p "$WSROOT"
    for C in "${COINS_ARR[@]}"; do
        echo "== workspace: $C =="
        python3 "$REPO_ROOT/scripts/prepare_wallet_variant.py" --coin "$C" --workspace "$WSROOT/$C"
    done
}

build_daemons() {
    local native_lib="$1" native_win out="$BUILD_ROOT/daemons-out" out_win C lc ws ws_win bin
    native_win="$(win_path "$native_lib")"
    out_win="$(win_path "$out")"
    rm -rf "$out"
    for C in "${COINS_ARR[@]}"; do
        lc="$(printf '%s' "$C" | tr 'A-Z' 'a-z')"
        ws="$WSROOT/$C"
        ws_win="$(win_path "$ws")"
        echo "== electrum-$lc (Windows, from $ws) =="
        ( cd "$ws" && PYTHONPATH="$ws_win" wine_python -m PyInstaller \
            --onedir --noconfirm --clean \
            --name "electrum-$lc" \
            --distpath "$out_win/dist" --workpath "$out_win/build/$C" --specpath "$out_win/spec/$C" \
            --collect-all electrum --collect-all electrum_ecc \
            --exclude-module PyQt5 --exclude-module PyQt6 \
            --add-binary "$native_win;." \
            run_electrum )
        bin="$out/dist/electrum-$lc/electrum-$lc.exe"
        [ -f "$bin" ] || { echo "daemon build failed: $C" >&2; exit 1; }
    done
}

build_supervisor() {
    local native_lib="$1" native_win out="$BUILD_ROOT/supervisor-out" out_win repo_win overlays_win launcher_win bin
    native_win="$(win_path "$native_lib")"
    out_win="$(win_path "$out")"
    repo_win="$(win_path "$REPO_ROOT")"
    overlays_win="$(win_path "$REPO_ROOT/coin-overlays")"
    launcher_win="$(win_path "$REPO_ROOT/unified/launcher.py")"
    rm -rf "$out"
    ( cd "$BUILD_ROOT" && PYTHONPATH="$repo_win" wine_python -m PyInstaller \
        --onedir --noconfirm --clean \
        --name electrum-backend \
        --distpath "$out_win/dist" --workpath "$out_win/build" --specpath "$out_win/spec" \
        --collect-all electrum --collect-all electrum_ecc \
        --collect-submodules unified \
        --collect-all argon2 \
        --exclude-module PyQt5 --exclude-module PyQt6 \
        --add-data "$overlays_win;coin-overlays" \
        --add-binary "$native_win;." \
        --paths "$repo_win" \
        "$launcher_win" )
    bin="$out/dist/electrum-backend/electrum-backend.exe"
    [ -f "$bin" ] || { echo "supervisor build failed" >&2; exit 1; }
}

stage_backend() {
    local b="$DESKTOP/backend" C lc
    rm -rf "$b"
    mkdir -p "$b/supervisor" "$b/daemons"
    cp -a "$BUILD_ROOT/supervisor-out/dist/electrum-backend" "$b/supervisor/"
    for C in "${COINS_ARR[@]}"; do
        lc="$(printf '%s' "$C" | tr 'A-Z' 'a-z')"
        cp -a "$BUILD_ROOT/daemons-out/dist/electrum-$lc" "$b/daemons/"
    done
}

build_desktop() {
    ( cd "$DESKTOP" \
        && npm install \
        && npm run build \
        && npx electron-builder --win portable nsis --x64 --config.directories.output=release )
}

stage_outputs() {
    local outdir="$REPO_ROOT/outputs/multiwallet/windows" release="$DESKTOP/release"
    rm -rf "$outdir"
    mkdir -p "$outdir"
    find "$release" -maxdepth 1 -type f \
        \( -name '*.exe' -o -name '*.blockmap' -o -name '*.yml' \) \
        -exec cp -f {} "$outdir/" \;
    if [ -d "$release/win-unpacked" ]; then
        cp -a "$release/win-unpacked" "$outdir/"
    fi
    find "$outdir" -maxdepth 2 -print | sort
}

ensure_wine_python
ensure_wine_deps
native_lib="$(build_blake256_dll)"
prepare_workspaces
build_daemons "$native_lib"
build_supervisor "$native_lib"
stage_backend
build_desktop
stage_outputs
echo "== multiwallet build complete: windows =="
