#!/usr/bin/env bash
# Build standalone per-coin Electrum daemon binaries (PyInstaller onedir) for the
# self-contained Blakestream Wallet AppImage. Run on the build server
# (192.168.1.221) inside a venv that has pyinstaller + the electrum runtime deps.
#
#   build-backend.sh <WORKSPACES_ROOT> <OUT_DIR> <BLAKE256_NATIVE_LIB> [COIN ...]
#
# Each per-coin workspace (WORKSPACES_ROOT/<COIN>) is a full Electrum source tree
# carrying that coin's constants; PYTHONPATH pins it so the bundle gets the right
# net. The blake256 C extension lives at the repo root, so its path is explicit.
set -euo pipefail

WSROOT="${1:?usage: build-backend.sh <workspaces-root> <out-dir> <blake256.so> [COIN...]}"
OUT="${2:?out dir}"
NATIVE_LIB="${3:?path to _blake256*.so or blake256.dll}"
shift 3
COINS=("$@"); [ ${#COINS[@]} -eq 0 ] && COINS=(BLC BBTC ELT LIT PHO UMO)
case "$(uname -s 2>/dev/null || printf unknown)" in
  MINGW*|MSYS*|CYGWIN*) EXE=".exe"; ADD_BINARY_SEP=";" ;;
  *)                    EXE="";     ADD_BINARY_SEP=":" ;;
esac

[ -f "$NATIVE_LIB" ] || { echo "blake256 native library not found: $NATIVE_LIB" >&2; exit 1; }

for C in "${COINS[@]}"; do
  WS="$WSROOT/$C"; LC=$(echo "$C" | tr 'A-Z' 'a-z')
  [ -x "$WS/run_electrum" ] || { echo "missing workspace: $WS/run_electrum" >&2; exit 1; }
  echo "== electrum-$LC (from $WS) =="
  ( cd "$WS" && PYTHONPATH="$WS" "${PYTHON_BIN:-python3}" -m PyInstaller --onedir --noconfirm --clean \
      --name "electrum-$LC" \
      --distpath "$OUT/dist" --workpath "$OUT/build/$C" --specpath "$OUT/spec/$C" \
      --collect-all electrum --collect-all electrum_ecc \
      --exclude-module PyQt5 --exclude-module PyQt6 \
      --add-binary "$NATIVE_LIB$ADD_BINARY_SEP." \
      run_electrum >/dev/null 2>&1 )
  bin="$OUT/dist/electrum-$LC/electrum-$LC$EXE"
  [ -x "$bin" ] && echo "  ok: $bin" || { echo "  FAILED: $C" >&2; exit 1; }
done
echo "built: ${COINS[*]}"
