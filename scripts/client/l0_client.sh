#!/usr/bin/env bash
# IP-327 __CLIENT_HOST__-side battery (copied from IP-310) for one boot: acceptance by workload -> thinking-off battery ->
# long-prefill fairness -> staggered admission. Sequential (single-stream cells need an idle engine).
# Usage: BASE=http://127.0.0.1:18000 ./l0_client.sh <LABEL>        # e.g. L0, L1, L2, L3a, L3b, L4
set -u
LABEL="${1:?label}"
D=~/.local/share/bench/ip327-20260924
P=$D/probes
OUT=$D/$LABEL; mkdir -p "$OUT"
export BASE="${BASE:-http://127.0.0.1:18000}"
log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
idle() {  # wait until nothing is running on the engine (max 10 min)
  for i in $(seq 1 120); do
    r=$(curl -s -m 5 "$BASE/metrics" | awk '/^vllm:num_requests_running\{/{print $2}')
    [ "${r:-1}" = "0.0" ] && return 0; sleep 5
  done; log "engine never went idle"; return 1
}
log "battery $LABEL start (BASE=$BASE)"; idle
log "1/4 acceptance by workload";      python3 "$P/accept_probe.py"       "$OUT/accept.json"      > "$OUT/accept.log" 2>&1;      tail -9 "$OUT/accept.log"; idle
log "2/4 thinking-off battery";        python3 "$P/l0_thinkoff_battery.py" "$LABEL" --out "$OUT" > "$OUT/thinkoff.log" 2>&1;   grep -E "^SUMMARY" "$OUT/thinkoff.log"; idle
log "3/4 long-prefill fairness";       python3 "$P/warmpoll_probe.py"     "$LABEL" --out "$OUT"    > "$OUT/warmpoll.log" 2>&1;   grep -A 14 '^{' "$OUT/warmpoll.log" | head -16; idle
log "4/4 staggered admission";         python3 "$P/stagger_probe.py"      "$LABEL" --out "$OUT"    > "$OUT/stagger.log" 2>&1;    tail -3 "$OUT/stagger.log"; idle
log "battery $LABEL done"
