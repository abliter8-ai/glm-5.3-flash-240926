#!/usr/bin/env bash
# IP-327: verify a booted arm by effect (ON __HEAD_HOST__). Env that reached BOTH containers, KV pool, transplant, warm-up,
# host floor, and one real greedy completion checked for content. Usage: ~/ip327/verify_boot.sh <ARM> <port>
set -u
ARM="${1:?arm}"; PORT="${2:-8001}"; L=~/ip327/logs/$ARM
echo "== container env (head | worker)"
for k in GLM53_EXL3_MOE_FAST GLM53_DRAFT_KV_COMPACT GLM53_SPINWAIT_MS GLM53_KDA_BF16_LARGE_M ABLIT ABLIT_METHOD \
         GLM53_DENSE_FP8 GLM53_ADAPTIVE_K GLM53_MIXED_PREFILL_CHUNK LIMIT_MM MM_IMAGE_TOKENS MM_PROCESSOR_CACHE_GB \
         DEFAULT_MAX_NEW_TOKENS LOAD_FORMAT NCCL_IB_MERGE_NICS; do
  h=$(docker inspect glm53-exl3-head --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -m1 "^$k=" | cut -d= -f2-)
  w=$(ssh -o BatchMode=yes __USER__@__WORKER_IP__ "docker inspect glm53-exl3-worker --format '{{range .Config.Env}}{{println .}}{{end}}'" | grep -m1 "^$k=" | cut -d= -f2-)
  printf '  %-28s %-22s | %s\n' "$k" "${h:-<unset>}" "${w:-<unset>}"
done
echo "== image (head | worker)"
echo "  $(docker inspect glm53-exl3-head --format '{{.Image}}' | cut -c1-19) | $(ssh -o BatchMode=yes __USER__@__WORKER_IP__ "docker inspect glm53-exl3-worker --format '{{.Image}}'" | cut -c1-19)"
echo "== boot log facts"
grep -a -E "GPU KV cache size|Maximum concurrency|boot-shape-warmup|glm53-kv-capacity" "$L/head.log" | tail -6 | cut -c1-220
grep -a -c "transplant" "$L/head.log" | sed 's/^/  transplant lines: /'
echo "== host floor now"
printf '  head %s MiB | worker %s MiB\n' "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" \
  "$(ssh -o BatchMode=yes __USER__@__WORKER_IP__ "awk '/MemAvailable/{print int(\$2/1024)}' /proc/meminfo")"
echo "== greedy completion (content check)"
python3 - "$PORT" <<'PY'
import json, sys, time, urllib.request
port = sys.argv[1]
body = {"model": "glm-5.3-flash-uncensored", "temperature": 0, "max_tokens": 200,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": "List the first five prime numbers, then name the capital of Ireland. One line each."}]}
t0 = time.time()
r = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
      data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=120))
c = r["choices"][0]; txt = c["message"].get("content") or ""
ok = all(p in txt for p in ("2", "3", "5", "7", "11")) and "Dublin" in txt
print(f"  {time.time()-t0:.1f}s finish={c['finish_reason']} completion_tokens={r['usage']['completion_tokens']} "
      f"reasoning_chars={len(c['message'].get('reasoning_content') or c['message'].get('reasoning') or '')} content_ok={ok}")
print("  " + txt.replace("\n", " | ")[:200])
PY
