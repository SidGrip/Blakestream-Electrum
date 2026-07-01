#!/usr/bin/env bash
# server/deploy-daemons.sh — deploy the six BlakeStream full-node daemons for ElectrumX.
#
# This is intentionally separate from deploy.sh:
#   - deploy-daemons.sh owns full-node containers and txindex-safe configs.
#   - deploy.sh owns ElectrumX compose generation once daemons are ready.
#
# The flow mirrors the MPOS deploy-bundle style, but the daemon config differs:
# ElectrumX requires txindex=1 and unpruned block data.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE=""
DRY_RUN=0
INSTALL_SYSTEM_DEPS=0
OPEN_FIREWALL=0
WAIT_RPC=180
WAIT_SYNC=1
BUILD_IMAGES=0
USE_BOOTSTRAP=0
DAEMON_DATA_ROOT="${DAEMON_DATA_ROOT:-/root}"
CONTAINER_PREFIX="${DAEMON_CONTAINER_PREFIX:-electrum-daemon}"
DAEMON_SOURCE_REF="${DAEMON_SOURCE_REF:-master}"
DAEMON_BUILD_ROOT="${DAEMON_BUILD_ROOT:-/root/blakestream-daemon-builds}"
DAEMON_BUILD_JOBS="${DAEMON_BUILD_JOBS:-}"
DAEMON_BUILD_CONCURRENCY="${DAEMON_BUILD_CONCURRENCY:-}"
BOOTSTRAP_SERIES="${BOOTSTRAP_SERIES:-25.2}"
BOOTSTRAP_URL="${BOOTSTRAP_URL:-https://bootstrap.blakestream.io}"
BOOTSTRAP_DOWNLOAD_ATTEMPTS="${BOOTSTRAP_DOWNLOAD_ATTEMPTS:-6}"
BOOTSTRAP_DOWNLOAD_RETRY_SLEEP_S="${BOOTSTRAP_DOWNLOAD_RETRY_SLEEP_S:-30}"
BOOTSTRAP_IMPORT_TIMEOUT_S="${BOOTSTRAP_IMPORT_TIMEOUT_S:-21600}"
BOOTSTRAP_IMPORT_SLEEP_S="${BOOTSTRAP_IMPORT_SLEEP_S:-60}"
DAEMON_SYNC_TIMEOUT_S="${DAEMON_SYNC_TIMEOUT_S:-7200}"
DAEMON_SYNC_POLL_SLEEP_S="${DAEMON_SYNC_POLL_SLEEP_S:-30}"
DAEMON_SYNC_TIP_LAG="${DAEMON_SYNC_TIP_LAG:-2}"

COINS=(blc bbtc elt lit pho umo)

declare -A COIN_LABEL=(
  [blc]="Blakecoin"
  [bbtc]="BlakeBitcoin"
  [elt]="Electron"
  [lit]="Lithium"
  [pho]="Photon"
  [umo]="Universal Molecule"
)
declare -A IMAGE_NAME=(
  [blc]="blakecoin"
  [bbtc]="blakebitcoin"
  [elt]="electron"
  [lit]="lithium"
  [pho]="photon"
  [umo]="universalmolecule"
)
declare -A SOURCE_REPO=(
  [blc]="https://github.com/BlueDragon747/Blakecoin.git"
  [bbtc]="https://github.com/BlakeBitcoin/BlakeBitcoin.git"
  [elt]="https://github.com/BlueDragon747/Electron-ELT.git"
  [lit]="https://github.com/BlueDragon747/lithium.git"
  [pho]="https://github.com/BlueDragon747/photon.git"
  [umo]="https://github.com/BlueDragon747/universalmol.git"
)
declare -A SOURCE_DIR=(
  [blc]="Blakecoin"
  [bbtc]="BlakeBitcoin"
  [elt]="Electron-ELT"
  [lit]="lithium"
  [pho]="photon"
  [umo]="universalmol"
)
declare -A DAEMON_BIN=(
  [blc]="blakecoind"
  [bbtc]="blakebitcoind"
  [elt]="electrond"
  [lit]="lithiumd"
  [pho]="photond"
  [umo]="universalmoleculed"
)
declare -A CLI_BIN=(
  [blc]="blakecoin-cli"
  [bbtc]="blakebitcoin-cli"
  [elt]="electron-cli"
  [lit]="lithium-cli"
  [pho]="photon-cli"
  [umo]="universalmolecule-cli"
)
declare -A DATADIR_NAME=(
  [blc]=".blakecoin"
  [bbtc]=".blakebitcoin"
  [elt]=".electron"
  [lit]=".lithium"
  [pho]=".photon"
  [umo]=".universalmolecule"
)
declare -A CONF_NAME=(
  [blc]="blakecoin.conf"
  [bbtc]="blakebitcoin.conf"
  [elt]="electron.conf"
  [lit]="lithium.conf"
  [pho]="photon.conf"
  [umo]="universalmolecule.conf"
)
declare -A RPC_PORT=(
  [blc]="8772"
  [bbtc]="8243"
  [elt]="6852"
  [lit]="12000"
  [pho]="8984"
  [umo]="5921"
)
declare -A P2P_PORT=(
  [blc]="8773"
  [bbtc]="8356"
  [elt]="6853"
  [lit]="12007"
  [pho]="35556"
  [umo]="24785"
)
declare -A BOOTSTRAP_PREFIX=(
  [blc]="blakecoin"
  [bbtc]="blakebitcoin"
  [elt]="electron"
  [lit]="lithium"
  [pho]="photon"
  [umo]="universalmolecule"
)

usage() {
  cat <<'EOF'
Usage: server/deploy-daemons.sh <mode> [options]

Deploy the six BlakeStream full-node daemons that ElectrumX needs.

Modes:
  --fresh              Create configs and start containers. Refuses to replace
                       existing managed containers unless --update is used.
  --update             Re-render configs, pull images, and recreate managed
                       containers in place.

Options:
  --install-system-deps Install Docker and basic host packages first.
  --build-images        Clone coin repos and build local daemon images before
                       starting containers. Defaults to local/<coin>:latest-local.
  --bootstrap           Download verified 25.2 bootstraps, import them one coin
                       at a time with -loadblock, then restart steady-state.
  --open-firewall       Open daemon P2P ports in ufw. RPC stays loopback-only.
  --wait-rpc SECONDS    Wait for each daemon RPC after start. Default: 180.
  --sync-timeout SECONDS Wait for each daemon to catch peer tip. Default: 7200.
  --no-wait-sync        Start daemons and return after RPC is reachable.
  --dry-run             Print planned actions without changing the host.
  -h, --help            Show this help.

Environment:
  DAEMON_DATA_ROOT      Parent directory for datadirs. Default: /root
  DAEMON_DOCKER_ORG     Docker org/user for daemon images. Default: sidgrip
  DAEMON_IMAGE_TAG      Docker tag for daemon images. Default: latest
  DAEMON_CONTAINER_PREFIX
                        Container name prefix. Default: electrum-daemon
  DAEMON_SOURCE_REF     Branch/tag for --build-images. Default: master
  DAEMON_BUILD_ROOT     Build working directory. Default:
                        /root/blakestream-daemon-builds
  DAEMON_BUILD_JOBS     Jobs passed to each coin build.sh. Default: CPU cores - 1
  DAEMON_BUILD_CONCURRENCY
                        Number of coin image builds at once. Default: CPU/2
  BOOTSTRAP_URL         Bootstrap base URL. Default:
                        https://bootstrap.blakestream.io
  BOOTSTRAP_SERIES      Bootstrap series path. Default: 25.2
  DAEMON_SYNC_TIMEOUT_S Seconds to wait per daemon for chain sync. Default: 7200
  DAEMON_SYNC_TIP_LAG   Accept synced when local height is this close to peer
                        tip/header height. Default: 2 blocks

Important:
  These configs are for ElectrumX, not MPOS. They set txindex=1 and do not
  prune. By default this script waits for daemon sync before returning, so
  server/deploy.sh can run next without --allow-unsynced-txindex.
EOF
}

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf '\n!! %s\n' "$*" >&2; }
die()  { printf '\nXX %s\n' "$*" >&2; exit "${2:-1}"; }

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    "$@"
  else
    printf '   [dry-run] not executed\n'
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh) MODE="fresh" ;;
    --update) MODE="update" ;;
    --install-system-deps) INSTALL_SYSTEM_DEPS=1 ;;
    --build-images) BUILD_IMAGES=1 ;;
    --bootstrap) USE_BOOTSTRAP=1 ;;
    --open-firewall) OPEN_FIREWALL=1 ;;
    --wait-rpc)
      WAIT_RPC="${2:?--wait-rpc requires seconds}"
      shift
      ;;
    --sync-timeout)
      DAEMON_SYNC_TIMEOUT_S="${2:?--sync-timeout requires seconds}"
      shift
      ;;
    --no-wait-sync) WAIT_SYNC=0 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" 2 ;;
  esac
  shift
done

[[ -n "${MODE}" ]] || die "Specify --fresh or --update." 2
[[ "$(id -u)" -eq 0 ]] || die "Run as root on the server." 1
[[ "${WAIT_RPC}" =~ ^[0-9]+$ ]] || die "--wait-rpc must be an integer." 2
[[ "${DAEMON_SYNC_TIMEOUT_S}" =~ ^[0-9]+$ ]] || die "--sync-timeout must be an integer." 2
[[ "${DAEMON_SYNC_POLL_SLEEP_S}" =~ ^[0-9]+$ ]] || die "DAEMON_SYNC_POLL_SLEEP_S must be an integer." 2
[[ "${DAEMON_SYNC_TIP_LAG}" =~ ^[0-9]+$ ]] || die "DAEMON_SYNC_TIP_LAG must be an integer." 2

if [[ "${BUILD_IMAGES}" -eq 1 ]]; then
  DOCKER_ORG="${DAEMON_DOCKER_ORG:-local}"
  IMAGE_TAG="${DAEMON_IMAGE_TAG:-latest-local}"
else
  DOCKER_ORG="${DAEMON_DOCKER_ORG:-sidgrip}"
  IMAGE_TAG="${DAEMON_IMAGE_TAG:-latest}"
fi

if [[ -z "${DAEMON_BUILD_JOBS}" ]]; then
  cpu_count="$(nproc 2>/dev/null || echo 2)"
  DAEMON_BUILD_JOBS=$((cpu_count > 1 ? cpu_count - 1 : 1))
fi
if [[ -z "${DAEMON_BUILD_CONCURRENCY}" ]]; then
  cpu_count="$(nproc 2>/dev/null || echo 2)"
  DAEMON_BUILD_CONCURRENCY=$((cpu_count / 2))
  [[ "${DAEMON_BUILD_CONCURRENCY}" -ge 1 ]] || DAEMON_BUILD_CONCURRENCY=1
  [[ "${DAEMON_BUILD_CONCURRENCY}" -le 3 ]] || DAEMON_BUILD_CONCURRENCY=3
fi

datadir_for() {
  local coin="$1"
  printf '%s/%s' "${DAEMON_DATA_ROOT%/}" "${DATADIR_NAME[$coin]}"
}

container_for() {
  local coin="$1"
  printf '%s-%s' "${CONTAINER_PREFIX}" "${coin}"
}

image_for() {
  local coin="$1"
  printf '%s/%s:%s' "${DOCKER_ORG}" "${IMAGE_NAME[$coin]}" "${IMAGE_TAG}"
}

install_system_deps() {
  log "installing system dependencies"
  export DEBIAN_FRONTEND=noninteractive
  run apt-get update
  run apt-get install -y --no-install-recommends \
    ca-certificates curl git jq openssl rsync ufw wget xz-utils \
    docker.io docker-compose-v2
  run systemctl enable --now docker
}

random_hex() {
  openssl rand -hex 24
}

write_config() {
  local coin="$1" datadir conf path rpc_user rpc_pass
  datadir="$(datadir_for "$coin")"
  conf="${CONF_NAME[$coin]}"
  path="${datadir}/${conf}"
  run mkdir -p "$datadir"

  if [[ -f "$path" ]]; then
    rpc_user="$(awk -F= '$1=="rpcuser"{print $2; exit}' "$path" || true)"
    rpc_pass="$(awk -F= '$1=="rpcpassword"{print $2; exit}' "$path" || true)"
  fi
  rpc_user="${rpc_user:-electrumx_${coin}}"
  rpc_pass="${rpc_pass:-$(random_hex)}"

  log "writing ${COIN_LABEL[$coin]} config at ${path}"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    cat > "$path" <<EOF
# ${COIN_LABEL[$coin]} configuration generated by Blakestream ElectrumX deploy.
server=1
daemon=0
listen=1
txindex=1
prune=0
rpcuser=${rpc_user}
rpcpassword=${rpc_pass}
rpcbind=127.0.0.1
rpcallowip=127.0.0.1
rpcport=${RPC_PORT[$coin]}
port=${P2P_PORT[$coin]}
dbcache=1024
maxmempool=100
fallbackfee=0.0001
EOF
    chmod 600 "$path"
  else
    printf '   [dry-run] would write %s\n' "$path"
  fi
}

pull_images() {
  local coin image
  log "pulling daemon images"
  for coin in "${COINS[@]}"; do
    image="$(image_for "$coin")"
    run docker pull "$image"
  done
}

sync_source_repo() {
  local coin="$1" repo dir
  repo="${SOURCE_REPO[$coin]}"
  dir="${DAEMON_BUILD_ROOT%/}/${SOURCE_DIR[$coin]}"
  run mkdir -p "$DAEMON_BUILD_ROOT"
  if [[ -d "${dir}/.git" ]]; then
    log "updating ${coin} source at ${dir}"
    run git -C "$dir" fetch --depth 1 origin "$DAEMON_SOURCE_REF"
    run git -C "$dir" checkout -B "$DAEMON_SOURCE_REF" FETCH_HEAD
    run git -C "$dir" reset --hard FETCH_HEAD
  else
    log "cloning ${coin} source from ${repo}"
    run rm -rf "$dir"
    run git clone --depth 1 --branch "$DAEMON_SOURCE_REF" "$repo" "$dir"
  fi
}

apply_daemon_build_policy() {
  local coin="$1" build_script
  build_script="${DAEMON_BUILD_ROOT%/}/${SOURCE_DIR[$coin]}/build.sh"
  [[ -f "$build_script" ]] || die "missing build script: ${build_script}" 1
  log "applying ${coin} daemon build policy"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    perl -0pi -e '
      s/(daemon\)\s+configure_extra="--with-gui=no)(?! --without-miniupnpc)/$1 --without-miniupnpc/g;
      s/^\s*libminiupnpc-dev\s*\n//mg;
      s/\s+libminiupnpc-dev\b//g;
    ' "$build_script"
    grep -Eq 'daemon\)[[:space:]]+configure_extra="--with-gui=no --without-miniupnpc' "$build_script" \
      || die "failed to add --without-miniupnpc to ${build_script}" 1
  else
    printf '   [dry-run] would patch %s\n' "$build_script"
  fi
}

build_coin_binaries() {
  local coin="$1" dir
  dir="${DAEMON_BUILD_ROOT%/}/${SOURCE_DIR[$coin]}"
  log "building ${coin} daemon binaries"
  run chmod +x "${dir}/build.sh"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    (
      cd "$dir"
      OUTPUT_BASE="${dir}/outputs" ./build.sh --native --daemon --pull-docker --jobs "$DAEMON_BUILD_JOBS"
    )
  else
    printf '   [dry-run] would run %s/build.sh --native --daemon --pull-docker --jobs %s\n' "$dir" "$DAEMON_BUILD_JOBS"
  fi
}

package_coin_image() {
  local coin="$1" dir output_dir image daemon cli tx
  dir="${DAEMON_BUILD_ROOT%/}/${SOURCE_DIR[$coin]}"
  output_dir="${dir}/outputs/Ubuntu-24"
  image="$(image_for "$coin")"
  daemon="${DAEMON_BIN[$coin]}"
  cli="${CLI_BIN[$coin]}"
  tx="${CLI_BIN[$coin]/-cli/-tx}"
  [[ "$coin" == "umo" ]] && tx="universalmolecule-tx"

  for bin in "$daemon" "$cli" "$tx"; do
    [[ -x "${output_dir}/${bin}" ]] || die "missing expected ${coin} build output: ${output_dir}/${bin}" 1
  done

  log "packaging ${coin} runtime image ${image}"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    docker build -t "$image" -f - "$output_dir" <<EOF
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        libboost-filesystem1.83.0 \
        libboost-program-options1.83.0 \
        libboost-thread1.83.0 \
        libboost-chrono1.83.0 \
        libevent-2.1-7t64 \
        libevent-pthreads-2.1-7t64 \
        libsqlite3-0 \
        libssl3 \
        libstdc++6 \
        libzmq5 \
    && rm -rf /var/lib/apt/lists/*
COPY ${daemon} /usr/local/bin/${daemon}
COPY ${cli} /usr/local/bin/${cli}
COPY ${tx} /usr/local/bin/${tx}
RUN chmod 0755 /usr/local/bin/${daemon} /usr/local/bin/${cli} /usr/local/bin/${tx}
EOF
    docker run --rm --entrypoint /bin/sh "$image" -lc \
      "/usr/local/bin/${daemon} --version >/dev/null 2>&1 || /usr/local/bin/${daemon} -version >/dev/null"
  else
    printf '   [dry-run] would docker build %s from %s\n' "$image" "$output_dir"
  fi
}

build_one_image() {
  local coin="$1" image
  image="$(image_for "$coin")"
  if [[ "${DRY_RUN}" -eq 0 ]] && docker image inspect "$image" >/dev/null 2>&1; then
    log "skipping ${coin} build; image ${image} already exists"
    return 0
  fi
  sync_source_repo "$coin"
  apply_daemon_build_policy "$coin"
  build_coin_binaries "$coin"
  package_coin_image "$coin"
}

build_images() {
  local coin pids=() failures=0 survivors=()
  log "building daemon images as ${DOCKER_ORG}/<coin>:${IMAGE_TAG}"
  for coin in "${COINS[@]}"; do
    while [[ "${#pids[@]}" -ge "$DAEMON_BUILD_CONCURRENCY" ]]; do
      wait -n || failures=$((failures + 1))
      survivors=()
      for pid in "${pids[@]}"; do
        kill -0 "$pid" 2>/dev/null && survivors+=("$pid")
      done
      pids=("${survivors[@]}")
    done
    ( build_one_image "$coin" ) &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || failures=$((failures + 1))
  done
  [[ "$failures" -eq 0 ]] || die "${failures} daemon image build(s) failed" 1
}

ensure_images() {
  if [[ "${BUILD_IMAGES}" -eq 1 ]]; then
    build_images
  else
    pull_images
  fi
}

stop_container() {
  local coin="$1" container cli datadir
  container="$(container_for "$coin")"
  cli="${CLI_BIN[$coin]}"
  datadir="$(datadir_for "$coin")"
  if ! docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$container"; then
    return 0
  fi
  log "stopping ${container}"
  run docker update --restart=no "$container"
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$container"; then
    docker exec "$container" "/usr/local/bin/${cli}" "-datadir=${datadir}" stop >/dev/null 2>&1 || true
    for _ in $(seq 1 120); do
      docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$container" || break
      sleep 5
    done
  fi
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$container"; then
    warn "${container} did not stop cleanly; forcing stop"
    run docker stop -t 120 "$container"
  fi
  run docker rm "$container"
}

start_container() {
  local coin="$1" loadblock="${2:-0}" container image daemon datadir loadblock_arg=""
  container="$(container_for "$coin")"
  image="$(image_for "$coin")"
  daemon="${DAEMON_BIN[$coin]}"
  datadir="$(datadir_for "$coin")"
  if [[ "$loadblock" == "1" ]]; then
    [[ -f "${datadir}/bootstrap.dat" ]] || die "missing ${datadir}/bootstrap.dat for ${coin} bootstrap import" 1
    loadblock_arg=" -loadblock='${datadir}/bootstrap.dat'"
  fi

  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$container"; then
    if [[ "${MODE}" == "fresh" ]]; then
      die "${container} already exists; use --update to recreate it." 3
    fi
    stop_container "$coin"
  fi

  log "starting ${container} from ${image}"
  run docker run -d \
    --name "$container" \
    --user 0:0 \
    --net=host \
    --restart=unless-stopped \
    --stop-timeout 600 \
    --entrypoint /bin/sh \
    -v "${datadir}:${datadir}" \
    "$image" \
    -lc "mkdir -p '${datadir}' && touch '${datadir}/debug.log' && exec /usr/local/bin/${daemon} -datadir='${datadir}'${loadblock_arg}"
}

wait_rpc() {
  local coin="$1" user pass port datadir conf started now timeout
  datadir="$(datadir_for "$coin")"
  conf="${datadir}/${CONF_NAME[$coin]}"
  user="$(awk -F= '$1=="rpcuser"{print $2; exit}' "$conf")"
  pass="$(awk -F= '$1=="rpcpassword"{print $2; exit}' "$conf")"
  port="${RPC_PORT[$coin]}"
  started="$(date +%s)"
  timeout="$WAIT_RPC"
  log "waiting for ${coin} RPC on 127.0.0.1:${port}"
  while true; do
    if curl -fsS --connect-timeout 2 --max-time 5 \
      --user "${user}:${pass}" \
      --data-binary '{"jsonrpc":"1.0","id":"deploy","method":"getblockcount","params":[]}' \
      -H 'content-type:text/plain;' \
      "http://127.0.0.1:${port}/" >/dev/null 2>&1; then
      printf '   %s RPC is responding\n' "$coin"
      return 0
    fi
    now="$(date +%s)"
    if (( now - started >= timeout )); then
      warn "${coin} RPC did not respond within ${timeout}s"
      return 1
    fi
    sleep 5
  done
}

rpc_result() {
  local coin="$1" method="$2" datadir conf user pass port
  datadir="$(datadir_for "$coin")"
  conf="${datadir}/${CONF_NAME[$coin]}"
  user="$(awk -F= '$1=="rpcuser"{print $2; exit}' "$conf")"
  pass="$(awk -F= '$1=="rpcpassword"{print $2; exit}' "$conf")"
  port="${RPC_PORT[$coin]}"
  curl -fsS --connect-timeout 3 --max-time 10 \
    --user "${user}:${pass}" \
    --data-binary "{\"jsonrpc\":\"1.0\",\"id\":\"deploy\",\"method\":\"${method}\",\"params\":[]}" \
    -H 'content-type:text/plain;' \
    "http://127.0.0.1:${port}/" | jq -r '.result'
}

abs_int() {
  local n="$1"
  if (( n < 0 )); then
    printf '%s' "$(( -n ))"
  else
    printf '%s' "$n"
  fi
}

peer_tip_for() {
  local coin="$1"
  rpc_result "$coin" getpeerinfo 2>/dev/null \
    | jq -r '[.[]? | .synced_headers?, .synced_blocks?, .startingheight?]
             | map(select(type == "number" and . > 0))
             | max // 0' 2>/dev/null \
    || printf '0'
}

wait_daemon_sync() {
  local coin="$1" started now info height headers ibd progress peer_tip tip delta abs_delta
  started="$(date +%s)"
  log "waiting for ${coin} to sync to peer/header tip"
  while true; do
    info="$(rpc_result "$coin" getblockchaininfo 2>/dev/null || true)"
    height="$(jq -r '.blocks // 0' <<<"$info" 2>/dev/null || echo 0)"
    headers="$(jq -r '.headers // 0' <<<"$info" 2>/dev/null || echo 0)"
    ibd="$(jq -r '.initialblockdownload // true' <<<"$info" 2>/dev/null || echo true)"
    progress="$(jq -r '.verificationprogress // 0' <<<"$info" 2>/dev/null || echo 0)"
    peer_tip="$(peer_tip_for "$coin")"

    [[ "$height" =~ ^[0-9]+$ ]] || height=0
    [[ "$headers" =~ ^[0-9]+$ ]] || headers=0
    [[ "$peer_tip" =~ ^[0-9]+$ ]] || peer_tip=0
    tip="$headers"
    (( peer_tip > tip )) && tip="$peer_tip"
    delta=$(( tip - height ))
    abs_delta="$(abs_int "$delta")"

    printf '   %s height=%s tip=%s delta=%s ibd=%s progress=%s\n' \
      "$coin" "$height" "$tip" "$delta" "$ibd" "$progress"

    if (( height > 0 && tip > 0 && abs_delta <= DAEMON_SYNC_TIP_LAG )); then
      printf '   %s synced at height %s\n' "$coin" "$height"
      return 0
    fi

    now="$(date +%s)"
    if (( now - started >= DAEMON_SYNC_TIMEOUT_S )); then
      warn "${coin} did not sync within ${DAEMON_SYNC_TIMEOUT_S}s"
      return 1
    fi
    sleep "$DAEMON_SYNC_POLL_SLEEP_S"
  done
}

wait_txindex_sync() {
  local coin="$1" started now indexes synced best
  started="$(date +%s)"
  log "waiting for ${coin} txindex"
  while true; do
    indexes="$(rpc_result "$coin" getindexinfo 2>/dev/null || true)"
    if [[ -z "$indexes" || "$indexes" == "null" ]]; then
      warn "${coin} getindexinfo is unavailable; 25.2 daemon RPC is required"
      return 1
    fi

    synced="$(jq -r '.txindex.synced // empty' <<<"$indexes" 2>/dev/null || true)"
    best="$(jq -r '.txindex.best_block_height // .txindex.best_block // empty' <<<"$indexes" 2>/dev/null || true)"
    if [[ "$synced" == "true" ]]; then
      printf '   %s txindex synced at %s\n' "$coin" "${best:-unknown height}"
      return 0
    fi

    printf '   %s txindex syncing at %s\n' "$coin" "${best:-unknown height}"
    now="$(date +%s)"
    if (( now - started >= DAEMON_SYNC_TIMEOUT_S )); then
      warn "${coin} txindex did not sync within ${DAEMON_SYNC_TIMEOUT_S}s"
      return 1
    fi
    sleep "$DAEMON_SYNC_POLL_SLEEP_S"
  done
}

wait_daemons_synced() {
  local coin failures=0
  [[ "${WAIT_SYNC}" -eq 1 ]] || {
    warn "sync wait disabled by --no-wait-sync"
    return 0
  }
  log "waiting for all daemons to sync"
  for coin in "${COINS[@]}"; do
    if wait_daemon_sync "$coin"; then
      wait_txindex_sync "$coin" || failures=$((failures + 1))
    else
      failures=$((failures + 1))
    fi
  done
  [[ "$failures" -eq 0 ]] || die "${failures} daemon sync wait(s) failed" 1
}

bootstrap_base() {
  local url="${BOOTSTRAP_URL%/}"
  case "$url" in
    */"$BOOTSTRAP_SERIES") printf '%s' "$url" ;;
    *) printf '%s/%s' "$url" "$BOOTSTRAP_SERIES" ;;
  esac
}

fetch_bootstrap_index() {
  curl -fsS --max-time 20 "$(bootstrap_base)/"
}

bootstrap_remote_file() {
  local coin="$1" index="$2" prefix
  prefix="${BOOTSTRAP_PREFIX[$coin]}"
  printf '%s' "$index" \
    | { grep -Eo "${prefix}-bootstrap-[0-9]+\\.dat\\.xz" || true; } \
    | sort -V \
    | tail -1
}

bootstrap_height_from_filename() {
  sed -nE 's/.*-bootstrap-([0-9]+)\.dat\.xz$/\1/p' <<<"$1"
}

download_bootstrap() {
  local coin="$1" index="$2" datadir remote_file base xz_file sha_file attempt
  datadir="$(datadir_for "$coin")"
  remote_file="$(bootstrap_remote_file "$coin" "$index")"
  [[ -n "$remote_file" ]] || die "no bootstrap file found for ${coin} at $(bootstrap_base)" 1
  base="$(bootstrap_base)"
  xz_file="${datadir}/${remote_file}"
  sha_file="${xz_file}.sha256"

  if [[ -f "${datadir}/bootstrap.dat" ]]; then
    log "${coin} bootstrap.dat already staged"
    return 0
  fi
  if [[ -f "${datadir}/bootstrap.dat.old" ]]; then
    log "${coin} bootstrap already consumed"
    return 0
  fi

  log "downloading ${coin} bootstrap ${remote_file}"
  for attempt in $(seq 1 "$BOOTSTRAP_DOWNLOAD_ATTEMPTS"); do
    if run wget -c -O "${xz_file}.tmp" "${base}/${remote_file}" \
      && run wget -O "${sha_file}.tmp" "${base}/${remote_file}.sha256"; then
      if [[ "${DRY_RUN}" -eq 0 ]]; then
        mv -f "${xz_file}.tmp" "$xz_file"
        mv -f "${sha_file}.tmp" "$sha_file"
        if ( cd "$datadir" && sha256sum -c "$(basename "$sha_file")" ); then
          break
        fi
        rm -f "$xz_file" "$sha_file"
      else
        break
      fi
    fi
    if [[ "$attempt" == "$BOOTSTRAP_DOWNLOAD_ATTEMPTS" ]]; then
      die "failed to download verified bootstrap for ${coin}" 1
    fi
    warn "${coin} bootstrap download attempt ${attempt} failed; retrying in ${BOOTSTRAP_DOWNLOAD_RETRY_SLEEP_S}s"
    sleep "$BOOTSTRAP_DOWNLOAD_RETRY_SLEEP_S"
  done

  log "decompressing ${coin} bootstrap"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    xz -dc "$xz_file" > "${datadir}/bootstrap.dat.tmp"
    mv -f "${datadir}/bootstrap.dat.tmp" "${datadir}/bootstrap.dat"
    bootstrap_height_from_filename "$remote_file" > "${datadir}/bootstrap.dat.height"
    rm -f "$xz_file" "$sha_file"
  else
    printf '   [dry-run] would xz -dc %s > %s/bootstrap.dat\n' "$xz_file" "$datadir"
  fi
}

download_bootstraps() {
  local index coin
  log "fetching bootstrap index from $(bootstrap_base)"
  index="$(fetch_bootstrap_index)"
  for coin in "${COINS[@]}"; do
    download_bootstrap "$coin" "$index"
  done
}

wait_bootstrap_height() {
  local coin="$1" datadir expected started now height ibd
  datadir="$(datadir_for "$coin")"
  expected="$(cat "${datadir}/bootstrap.dat.height" 2>/dev/null || true)"
  [[ "$expected" =~ ^[0-9]+$ ]] || {
    warn "${coin} has no bootstrap height metadata; waiting only for RPC"
    wait_rpc "$coin"
    return $?
  }
  started="$(date +%s)"
  log "waiting for ${coin} bootstrap import to reach height ${expected}"
  while true; do
    height="$(rpc_result "$coin" getblockcount 2>/dev/null || echo 0)"
    [[ "$height" =~ ^[0-9]+$ ]] || height=0
    ibd="$(rpc_result "$coin" getblockchaininfo 2>/dev/null | jq -r '.initialblockdownload // "unknown"' 2>/dev/null || echo unknown)"
    printf '   %s height=%s/%s ibd=%s\n' "$coin" "$height" "$expected" "$ibd"
    if (( height >= expected )); then
      return 0
    fi
    now="$(date +%s)"
    if (( now - started >= BOOTSTRAP_IMPORT_TIMEOUT_S )); then
      die "${coin} bootstrap import timed out before height ${expected}" 1
    fi
    sleep "$BOOTSTRAP_IMPORT_SLEEP_S"
  done
}

import_bootstraps() {
  local coin
  [[ "${DRY_RUN}" -eq 0 ]] || {
    log "dry-run bootstrap import skipped"
    return 0
  }
  log "importing bootstraps one coin at a time"
  for coin in "${COINS[@]}"; do
    stop_container "$coin"
    if [[ -f "$(datadir_for "$coin")/bootstrap.dat" ]]; then
      start_container "$coin" 1
      wait_rpc "$coin" || die "${coin} RPC did not come up for bootstrap import" 1
      wait_bootstrap_height "$coin"
      stop_container "$coin"
      mv -f "$(datadir_for "$coin")/bootstrap.dat" "$(datadir_for "$coin")/bootstrap.dat.old"
      [[ -f "$(datadir_for "$coin")/bootstrap.dat.height" ]] \
        && mv -f "$(datadir_for "$coin")/bootstrap.dat.height" "$(datadir_for "$coin")/bootstrap.dat.old.height"
    else
      log "${coin} bootstrap already consumed or missing; skipping import"
    fi
  done
  log "starting all daemons after bootstrap import"
  for coin in "${COINS[@]}"; do
    start_container "$coin"
  done
}

open_firewall() {
  local coin port
  command -v ufw >/dev/null 2>&1 || return 0
  log "opening daemon P2P ports in ufw"
  for coin in "${COINS[@]}"; do
    port="${P2P_PORT[$coin]}"
    run ufw allow "${port}/tcp"
  done
}

main() {
  if [[ "${INSTALL_SYSTEM_DEPS}" -eq 1 ]]; then
    install_system_deps
  fi
  command -v docker >/dev/null 2>&1 || die "docker is not installed; rerun with --install-system-deps." 1
  run systemctl enable --now docker

  local coin
  for coin in "${COINS[@]}"; do
    write_config "$coin"
  done
  ensure_images
  if [[ "${USE_BOOTSTRAP}" -eq 1 ]]; then
    download_bootstraps
    import_bootstraps
  else
    for coin in "${COINS[@]}"; do
      start_container "$coin"
    done
  fi
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    local failures=0
    for coin in "${COINS[@]}"; do
      wait_rpc "$coin" || failures=$((failures + 1))
    done
    if (( failures > 0 )); then
      warn "${failures} daemon RPC probe(s) did not answer yet. Check docker logs and rerun --update."
    fi
    wait_daemons_synced
  fi
  if [[ "${OPEN_FIREWALL}" -eq 1 ]]; then
    open_firewall
  fi
  log "daemon deploy step complete"
  printf 'Next: run server/deploy.sh --fresh --build-local\n'
}

main "$@"
