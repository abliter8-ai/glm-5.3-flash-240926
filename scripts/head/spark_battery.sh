#!/usr/bin/env bash
# IP-327 __HEAD_HOST__-side battery for one arm (run ON __HEAD_HOST__, lane on :8001).
# Usage: ~/ip327/spark_battery.sh <LABEL> <TREE> <full|control|kda>
#   full    = IP-310 l0_spark set (+c2 decode, EngineCore CPU, repeat-prefix) + prefill ladder + 3x250k floor stress
#   control = decode c1/c2 + c12 + EngineCore CPU + repeat-prefix + prefill ladder + 3x250k floor stress
#   kda     = decode c1 structured + prefill ladder + 3x250k floor stress
set -u
LABEL="${1:?label}"; TREE="${2:?tree}"; MODE="${3:?mode}"
RAW=~/ip327/raw/$LABEL; mkdir -p "$RAW"
P=~/ip327/probes; PORT=8001
HARNESS=~/glm53-exl3-overlay-0f49cfd   # one harness for every arm (HEAD bench_decode, RUIN-DELTA §3 constants)
log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
idle() { for i in $(seq 1 120); do r=$(curl -s -m 5 http://127.0.0.1:$PORT/metrics | awk '/^vllm:num_requests_running\{/{print $2}'); [ "${r:-1}" = "0.0" ] && return 0; sleep 5; done; log "engine never went idle"; return 1; }
ecpu() {  # summed CPU seconds (utime+stime) of the head EngineCore process(es)
  local s=0; for pid in $(pgrep -f "EngineCore" ); do
    set -- $(awk '{print $14, $15}' /proc/$pid/stat 2>/dev/null); s=$(( s + ${1:-0} + ${2:-0} )); done
  echo $s; }
memwatch() {  # sample MemAvailable on both nodes every 2 s until the flag file disappears
  local out="$1"; : > "$out"
  while [ -e "$RAW/.memwatch" ]; do
    printf '%s %s %s\n' "$(date -u +%H:%M:%S)" "$(awk '/MemAvailable/{print $2}' /proc/meminfo)" \
      "$(ssh -o BatchMode=yes __USER__@__WORKER_IP__ "awk '/MemAvailable/{print \$2}' /proc/meminfo")" >> "$out"
    sleep 2
  done; }
memmin() { awk 'NR==1{h=$2;w=$3} {if($2<h)h=$2; if($3<w)w=$3} END{printf "min MemAvailable head %.2f GiB worker %.2f GiB (%d samples)\n", h/1048576, w/1048576, NR}' "$1"; }
HZ=$(getconf CLK_TCK)
log "battery $LABEL ($MODE) start, tree $TREE"
docker logs glm53-exl3-head 2>&1 | grep -a -E "Available KV cache memory|GPU KV cache size|Maximum concurrency|glm53-kv-capacity-log|adaptive-k|dense fp8|EXL3 knobs|fast.moe|FAST|KDA|compact|spinwait|ABLIT_METHOD|transplant" | tail -24 > "$RAW/boot-facts.txt"
nvidia-smi --query-gpu=clocks.sm,clocks.max.sm --format=csv,noheader > "$RAW/clocks-start.txt"
idle
log "decode: bench_decode structured c1 x5 (+EngineCore CPU)"
c0=$(ecpu); t0=$(date +%s.%N)
( cd "$HARNESS" && python3 tests/bench_decode.py --phase structured --structured --runs 5 --max-tokens 400 --skip-coherence --out "$RAW/structured-c1.json" ) > "$RAW/structured-c1.log" 2>&1
c1=$(ecpu); t1=$(date +%s.%N)
python3 -c "print('EngineCore CPU over structured c1: %.1f%% of one core (%.1f s CPU / %.1f s wall)' % (100*($c1-$c0)/$HZ/($t1-$t0), ($c1-$c0)/$HZ, $t1-$t0))" | tee "$RAW/enginecore-cpu.txt"
tail -3 "$RAW/structured-c1.log"; idle
if [ "$MODE" != kda ]; then
  log "decode: bench_decode structured c2 x5"
  ( cd "$HARNESS" && python3 tests/bench_decode.py --phase structured --structured --runs 5 --max-tokens 400 --skip-coherence --concurrency 2 --out "$RAW/structured-c2.json" ) > "$RAW/structured-c2.log" 2>&1; tail -3 "$RAW/structured-c2.log"; idle
  log "conc_probe c12 x3"
  ( cd "$HARNESS/tests" && python3 $P/conc_probe.py 12 3 "$RAW/conc12.json" ) > "$RAW/conc12.log" 2>&1; tail -3 "$RAW/conc12.log"; idle
  log "repeat-prefix probe (compact KV claim)"
  python3 $P/repeat_prefix_probe.py "$RAW/repeat-prefix.json" 3 > "$RAW/repeat-prefix.log" 2>&1; grep SUMMARY "$RAW/repeat-prefix.log"; idle
fi
if [ "$MODE" = full ]; then
  log "needles 0 1"; python3 $P/ip286_needle.py 0 1 > "$RAW/needles.log" 2>&1; tail -4 "$RAW/needles.log"; idle
  log "ablit probe"; python3 $P/ip286_ablit.py > "$RAW/ablit.log" 2>&1; tail -4 "$RAW/ablit.log"; idle
fi
log "cold prefill ladder"
BASE=http://127.0.0.1:$PORT python3 $P/prefill_ladder.py "$LABEL" > "$RAW/prefill-ladder.log" 2>&1; tail -5 "$RAW/prefill-ladder.log"; idle
log "3x250k long-prefill stress with host-floor sampling"
touch "$RAW/.memwatch"; memwatch "$RAW/mem-stress.txt" & MW=$!
python3 $P/longprefill_stress.py "$RAW/longprefill-stress.json" > "$RAW/longprefill-stress.log" 2>&1
rm -f "$RAW/.memwatch"; wait $MW 2>/dev/null
tail -4 "$RAW/longprefill-stress.log"; memmin "$RAW/mem-stress.txt" | tee "$RAW/mem-stress-min.txt"
nvidia-smi --query-gpu=clocks.sm,clocks.max.sm --format=csv,noheader > "$RAW/clocks-end.txt"
log "host floor now: $(free -m | awk '/^Mem:/{print $7" MB available"}')"
log "battery $LABEL done"
