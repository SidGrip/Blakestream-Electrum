#!/usr/bin/env bash
# Build + push the prebuilt Electrium Windows (wine) base image.
# Run after `docker login` (for the push).
#
#   build-base.sh [image:tag]      (default: sidgrip/electrum-wine-base:25.2)
#
# Use --no-push to build only.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="sidgrip/electrum-wine-base:25.2"
PUSH=1
for a in "$@"; do
    case "$a" in
        --no-push) PUSH=0 ;;
        *) IMG="$a" ;;
    esac
done

echo "== building $IMG from $HERE/Dockerfile.base =="
docker build -f "$HERE/Dockerfile.base" -t "$IMG" "$HERE"
if [ "$PUSH" = "1" ]; then
    echo "== pushing $IMG =="
    docker push "$IMG"
fi
echo "== done: $IMG =="
