#!/usr/bin/env bash
# IP-327: boot one arm of the GLM-5.3-Flash EXL3 lane on __HEAD_HOST__ (head) + __WORKER_HOST__ (worker).
# Adapted from IP-314 boot_arm.sh. Never calls the overlay's stop/restart (docker rm -f); takedown is
# ~/safe-takedown.sh --peer __WORKER_HOST__ only (spark-safe-ops L0), and it refuses to take down under load.
# Usage: ~/ip327/boot_arm.sh <arm> <tree> <port 8000|8001> <kda 0|1> [kv_cache_bytes]
#   kv_cache_bytes (optional) replaces only the --kv-cache-memory-bytes value of the tree's EXTRA_ARGS,
#   exported as a caller override (start.sh lets caller exports win over .env); the .env is not edited.
set -euo pipefail
arm=${1:?arm}; tree=${2:?tree}; port=${3:?port}; kda=${4:?kda 0/1}; kvb=${5:-}
[[ $arm =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ $port == 8000 || $port == 8001 ]] || exit 2
[[ $kda == 0 || $kda == 1 ]] || exit 2
[[ -z $kvb || $kvb =~ ^[0-9]{10,11}$ ]] || exit 2
[ -x "$tree/start.sh" ] || { echo "no start.sh in $tree"; exit 2; }
[ "$(ls "$tree/ablit/transplant" | wc -l)" = 32 ] || { echo "transplant set missing in $tree"; exit 2; }
logroot="$HOME/ip327/logs/$arm"
mkdir -p "$logroot"
date -u +%FT%TZ > "$logroot/begin"
python3 - <<'PY'
import time, urllib.request, urllib.error, re
# Refuse takedown under admitted load on either port.
for port in (8000, 8001):
    for attempt in range(180):
        try:
            text = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
        except (urllib.error.URLError, ConnectionError, OSError):
            break
        vals = [float(x.rsplit(" ", 1)[1]) for x in text.splitlines() if re.match(r"^vllm:num_requests_(running|waiting)\{", x)]
        if len(vals) == 2 and sum(vals) == 0:
            break
        time.sleep(1)
    else:
        raise SystemExit("cannot drain existing GLM arm")
PY
"$HOME/safe-takedown.sh" --peer __WORKER_HOST__ 2>&1 | tee "$logroot/takedown.log"
test -z "$(docker ps --filter name=glm53-exl3 --format '{{.ID}}')"
test -z "$(ssh __WORKER_HOST__ "docker ps --filter name=glm53-exl3 --format '{{.ID}}'")"
# Keep the exited containers' logs, then remove the STOPPED containers (never a live GPU owner).
docker logs glm53-exl3-head > "$logroot/previous-head.log" 2>&1 || true
ssh __WORKER_HOST__ 'docker logs glm53-exl3-worker 2>&1' > "$logroot/previous-worker.log" || true
docker rm glm53-exl3-head >/dev/null 2>&1 || true
ssh __WORKER_HOST__ 'docker rm glm53-exl3-worker >/dev/null 2>&1 || true'
cat /proc/meminfo > "$logroot/preboot-head-meminfo"
ssh __WORKER_HOST__ 'cat /proc/meminfo' > "$logroot/preboot-worker-meminfo"
python3 - "$HOME/.cache/vllm-glm53-flash/glm53_adaptive_k.json" <<'PY'
import json, sys
x = json.load(open(sys.argv[1]))
assert x["mode"] == "ema" and x["set"] == "2,4,7" and float(x["margin"]) == 2.0, x
PY
cd "$tree"
# start.sh @0f49cfd resets ABLIT=0 after .env; caller exports survive. Harmless on the f906ee99 tree.
export PORT="$port" ABLIT=1 ABLIT_METHOD=transplant GLM53_KDA_BF16_LARGE_M="$kda"
if [ -n "$kvb" ]; then
    ea=$(bash -c 'set -a; . ./.env >/dev/null 2>&1; printf "%s" "$EXTRA_ARGS"')
    grep -q -- "--kv-cache-memory-bytes [0-9]" <<<"$ea" || { echo "no KV pin in EXTRA_ARGS"; exit 2; }
    export EXTRA_ARGS="$(sed -E "s/--kv-cache-memory-bytes [0-9]+/--kv-cache-memory-bytes $kvb/" <<<"$ea")"
    echo "IP327: EXTRA_ARGS override: $EXTRA_ARGS" | tee "$logroot/extra-args"
fi
export SKIP_PULL=1 SKIP_BUILD=1 SKIP_SHIP=1 SKIP_DOWNLOAD=1 SKIP_SYNC=1
date -u +%FT%TZ > "$logroot/launch"
./start.sh start 2>&1 | tee "$logroot/launcher.log"
if grep -q "boot shape warmup incomplete" "$logroot/launcher.log"; then
    echo "IP327: warmup failed; arm is not qualified" >&2
    exit 8
fi
date -u +%FT%TZ > "$logroot/launcher-returned"
docker logs glm53-exl3-head > "$logroot/head.log" 2>&1
ssh __WORKER_HOST__ 'docker logs glm53-exl3-worker 2>&1' > "$logroot/worker.log"
cat /proc/meminfo > "$logroot/postboot-head-meminfo"
ssh __WORKER_HOST__ 'cat /proc/meminfo' > "$logroot/postboot-worker-meminfo"
# Boot facts that must be present: runtime transplant really applied, KV pool and capacity lines.
{
  grep -a -E "ABLIT_METHOD|transplant|rel_l2" "$logroot/head.log" | tail -4
  grep -a -E "GPU KV cache size|Maximum concurrency|glm53-kv-capacity-log|Available KV cache memory" "$logroot/head.log" | tail -4
  grep -a -E "EXL3_MOE_FAST|glm53_fast_moe|thin-decode|KDA_BF16|kda.*bf16|DRAFT_KV_COMPACT|compact|spinwait|adaptive-k|dense fp8" "$logroot/head.log" | tail -10
} > "$logroot/boot-facts.txt" 2>&1 || true
grep -q -E "transplant" "$logroot/boot-facts.txt" || { echo "IP327: no transplant line in head log" >&2; exit 9; }
date -u +%FT%TZ > "$logroot/ready"
echo "IP327 arm $arm ready on :$port"
