#!/usr/bin/env bash
# IP-327 (copied from IP-310): the IP-286 posture-B LiveCodeBench trio, three harness instances in parallel (the lane serves c12).
# Usage: ./lcb_trio.sh <LABEL> <on|off> <max_tokens> [effort]     e.g. ./lcb_trio.sh L0 off 16384
set -u
LABEL="${1:?label}"; THINK="${2:?on|off}"; CAP="${3:?max tokens}"; EFFORT="${4:-}"
REPO=__FLEET_REPO__
D=~/.local/share/bench/ip327-20260924/$LABEL; mkdir -p "$D"
BASE="${BASE:-http://127.0.0.1:18000/v1}"
EXTRA=(); [ "$THINK" = "on" ] && [ -n "$EFFORT" ] && EXTRA=(--reasoning-effort "$EFFORT")
echo "[$(date -u +%H:%M:%SZ)] lcb trio $LABEL thinking=$THINK cap=$CAP effort=${EFFORT:-none}"
pids=()
for q in abc390_f abc397_e abc397_g; do
  ( cd "$REPO" && python3 tests/bench/livecodebench_suite.py --base-url "$BASE" --model glm-5.3-flash-uncensored \
      --mode direct --host __HEAD_HOST__ --question-ids "$q" --thinking "$THINK" --max-tokens "$CAP" \
      --temperature 0.2 --top-p 0.95 --label "$LABEL-think$THINK-$q" ${EXTRA[@]+"${EXTRA[@]}"} \
      --out "$D/lcb-$q.json" --progress "$D/lcb-$q.progress" ) > "$D/lcb-$q.log" 2>&1 &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "$p"; done
echo "[$(date -u +%H:%M:%SZ)] trio done"
for q in abc390_f abc397_e abc397_g; do
  python3 - "$D/lcb-$q.json" <<'PY'
import json,sys
p=sys.argv[1]
try:
    d=json.load(open(p))
except Exception as e:
    print(p, "NO RESULT:", e); sys.exit()
probs=d.get("problems") or []
for pr in probs:
    gens=pr.get("generations") or pr.get("samples") or []
    g=gens[0] if gens else {}
    print(f"{pr.get('question_id')}: passed={pr.get('passed', pr.get('pass_at_1'))} finish={g.get('finish_reason')} "
          f"completion_tokens={g.get('completion_tokens')} reasoning_chars={len(g.get('reasoning') or '')} content_chars={len(g.get('content') or '')}")
PY
done
