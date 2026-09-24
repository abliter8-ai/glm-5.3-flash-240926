#!/usr/bin/env bash
# IP-327: the rest of one arm's standard battery, run from __CLIENT_HOST__ once the __HEAD_HOST__-side battery has finished:
# [c12 rerun] -> l0_client.sh (acceptance, thinking-off, fairness, stagger) -> LCB trio (thinking off, 16k) -> vision ladder.
# Usage: arm_rest.sh <ARM> "<vision rungs>" <c12rerun 0|1>        e.g. arm_rest.sh A "1 8" 1
set -u
ARM="${1:?arm}"; RUNGS="${2:-1 8}"; C12="${3:-0}"
D=~/.local/share/bench/ip327-20260924; P=$D/probes
log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
until ssh -o ConnectTimeout=8 __HEAD_HOST__ "grep -q 'battery $ARM done' ~/ip327/logs/$ARM-battery.out"; do sleep 20; done
log "spark battery $ARM done"
if [ "$C12" = 1 ]; then
  log "conc_probe c12 x3 (rerun)"
  ssh __HEAD_HOST__ "cd ~/glm53-exl3-overlay-0f49cfd/tests && python3 ~/ip327/probes/conc_probe.py 12 3 ~/ip327/raw/$ARM/conc12.json > ~/ip327/raw/$ARM/conc12.log 2>&1; tail -3 ~/ip327/raw/$ARM/conc12.log"
fi
BASE=http://127.0.0.1:18000 "$P/l0_client.sh" "$ARM"
BASE=http://127.0.0.1:18000/v1 "$P/lcb_trio.sh" "$ARM" off 16384
ssh __HEAD_HOST__ "~/ip327/vision_check.sh $ARM '$RUNGS'"
log "arm $ARM remainder done"
