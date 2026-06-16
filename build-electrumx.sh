#!/usr/bin/env bash
# Build (and push) the Blakestream ElectrumX server Docker image. One shared
# image serves all six coins; deploy as six per-coin instances (server/docker-compose.yml).
#
#   build-electrumx.sh [image:tag] [--no-push] [--smoke]
#
# Default image: sidgrip/electrumx-blakestream:25.2.  Run after `docker login`
# (for the push). --smoke imports all 6 coin classes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="sidgrip/electrumx-blakestream:25.2"
PUSH=1; SMOKE=0
for a in "$@"; do
    case "$a" in
        --no-push) PUSH=0 ;;
        --smoke) SMOKE=1 ;;
        -*) echo "unknown flag: $a" >&2; exit 1 ;;
        *) IMG="$a" ;;
    esac
done

# Minimal build context (server/ + blake256/ only) so the multi-GB outputs/ tree
# is never sent to the docker daemon.
CTX="$(mktemp -d)"; trap 'rm -rf "$CTX"' EXIT
rsync -a --exclude '__pycache__/' --exclude '*.pyc' --exclude 'build/' \
      --exclude 'dist/' --exclude '*.egg-info/' "$REPO_ROOT/server/"   "$CTX/server/"
rsync -a --exclude '__pycache__/' --exclude '*.pyc' --exclude 'build/' "$REPO_ROOT/blake256/" "$CTX/blake256/"
cp "$REPO_ROOT/server/Dockerfile.blakestream" "$CTX/Dockerfile"

echo "== building $IMG =="
docker build -t "$IMG" "$CTX"

if [ "$SMOKE" = "1" ]; then
    echo "== smoke: import the 6 BlakeStream coin classes =="
    docker run --rm "$IMG" python3 -c \
        "from electrumx.lib.coins import Blakecoin, BlakeBitcoin, ElectronELT, Lithium, Photon, UniversalMolecule; print('coin classes OK:', [c.NAME for c in (Blakecoin, BlakeBitcoin, ElectronELT, Lithium, Photon, UniversalMolecule)])"
    echo "== smoke: electrumx_server present =="
    docker run --rm "$IMG" sh -c "command -v electrumx_server && python3 -c 'import electrumx; print(\"electrumx import OK\")'"
fi

if [ "$PUSH" = "1" ]; then
    echo "== pushing $IMG =="
    docker push "$IMG"
fi
echo "== done: $IMG =="
