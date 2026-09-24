#!/usr/bin/env bash
# IP-327 vision memory ladder for one arm (ON __HEAD_HOST__, lane on :8001). Rungs run in order with MemAvailable sampled
# on both nodes every 2 s. Before each rung above 8 the minimum is projected as
#   MemAvailable now - 1.25 x (previous rung's drop from its own baseline) x (n / previous n)
# In-flight image copies scale with the images IN THE REQUEST, so the drop is scaled by n/prev (an upper bound once
# rung 1 has paid the first-use cost). The ladder stops if either node would fall below 3 GiB (earlyoom -m 2 acts
# near 2.4 GiB on these nodes).
# Rung 49 must be REJECTED with HTTP 400 naming the 48-image cap (candidate only).
# Usage: ~/ip327/vision_check.sh <LABEL> "<rungs>"   control: "1 8"   candidate: "1 8 16 24 32 40 48 49"
set -u
LABEL="${1:?label}"; RUNGS="${2:-1 8}"; RAW=~/ip327/raw/$LABEL; mkdir -p "$RAW"; P=~/ip327/probes
FLOOR=$((3 * 1048576))   # KiB
log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
now() { printf '%s %s\n' "$(awk '/MemAvailable/{print $2}' /proc/meminfo)" \
  "$(ssh -o BatchMode=yes __USER__@__WORKER_IP__ "awk '/MemAvailable/{print \$2}' /proc/meminfo")"; }
mib() { echo $(( $1 / 1024 )); }
prev_dh=0; prev_dw=0; prev_n=1; ok=1
for n in $RUNGS; do
  read bh bw < <(now)
  if [ "$n" -gt 8 ] && [ "$n" != 49 ]; then
    ph=$(( bh - prev_dh * 5 * n / (4 * prev_n) )); pw=$(( bw - prev_dw * 5 * n / (4 * prev_n) ))
    log "rung $n: now head $(mib $bh) / worker $(mib $bw) MiB; projected min head $(mib $ph) / worker $(mib $pw) MiB"
    if [ $ph -lt $FLOOR ] || [ $pw -lt $FLOOR ]; then log "STOP before rung $n: projection below the 3 GiB floor"; ok=0; break; fi
  fi
  runs=1; [ "$n" = 8 ] && runs=3
  touch "$RAW/.vwatch"
  ( : > "$RAW/mem-vision-$n.txt"; while [ -e "$RAW/.vwatch" ]; do
      printf '%s %s\n' "$(date -u +%H:%M:%S)" "$(now)" >> "$RAW/mem-vision-$n.txt"; sleep 2; done ) & MW=$!
  python3 $P/ip314_acceptance.py --base-url http://127.0.0.1:8001 --arm "$LABEL" --case vision --vision-max-images "$n" \
    --runs "$runs" --out "$RAW/vision-$n.json" > "$RAW/vision-$n.log" 2>&1; rc=$?
  rm -f "$RAW/.vwatch"; wait $MW 2>/dev/null
  read mh mw < <(awk -v h="$bh" -v w="$bw" '{if($2<h)h=$2; if($3<w)w=$3} END{print h, w}' "$RAW/mem-vision-$n.txt")
  prev_dh=$(( bh - mh )); prev_dw=$(( bw - mw )); [ "$n" != 49 ] && prev_n=$n
  log "rung $n x$runs rc=$rc: min head $(mib $mh) / worker $(mib $mw) MiB (drop $(mib $prev_dh) / $(mib $prev_dw) MiB) | $(tail -1 "$RAW/vision-$n.log" | cut -c1-150)"
  if [ $mh -lt $FLOOR ] || [ $mw -lt $FLOOR ]; then log "FLOOR BREACH at rung $n"; ok=0; break; fi
done
log "vision ladder $LABEL done (ok=$ok)"
