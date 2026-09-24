#!/usr/bin/env python3
"""Aggregate decode at concurrency N — same cell as the IP-285 knee curve.
MiaAI's STRUCTURED_PROMPT, 400 tokens, T=0, thinking off, all N submitted at t=0, 3 runs.
Aggregate tok/s = sum(completion_tokens) / wall from first submit to last finish (the IP-285 definition).
Runs ON __HEAD_HOST__ next to tests/bench_decode.py.  python3 conc_probe.py <N> [runs] [out.json]
"""
import json, sys, time, threading, urllib.request
sys.path.insert(0, "~/glm53-exl3-overlay-0f49cfd/tests")  # IP-327: shared harness on :8001
from bench_decode import STRUCTURED_PROMPT, BASE  # noqa: E402

N = int(sys.argv[1]); RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUT = sys.argv[3] if len(sys.argv) > 3 else f"/tmp/exl3-conc{N}-chunk1024.json"
MODEL = json.load(urllib.request.urlopen(BASE + "/v1/models"))["data"][0]["id"]


def one(res, i):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": STRUCTURED_PROMPT}],
                       "max_tokens": 400, "temperature": 0,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    t0 = time.time()
    d = json.load(urllib.request.urlopen(urllib.request.Request(
        BASE + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"}), timeout=600))
    res[i] = {"tok": d["usage"]["completion_tokens"], "s": round(time.time() - t0, 2)}


# warm once (their harness warms too)
one({}, 0)
runs = []
for r in range(RUNS):
    res = [None] * N
    t0 = time.time()
    th = [threading.Thread(target=one, args=(res, i)) for i in range(N)]
    [t.start() for t in th]; [t.join() for t in th]
    wall = time.time() - t0
    tot = sum(x["tok"] for x in res)
    runs.append({"aggregate_tps": round(tot / wall, 1), "per_stream_tps": round(tot / wall / N, 1),
                 "wall_s": round(wall, 1), "total_tok": tot, "per_req": res})
    print(f"c{N} run {r+1}: aggregate {runs[-1]['aggregate_tps']} tok/s  per-stream {runs[-1]['per_stream_tps']}  wall {runs[-1]['wall_s']} s  tokens {tot}")
med = sorted(x["aggregate_tps"] for x in runs)[len(runs) // 2]
print(f"c{N} MEDIAN aggregate {med} tok/s  (runs: {[x['aggregate_tps'] for x in runs]})")
json.dump({"concurrency": N, "model": MODEL, "median_aggregate_tps": med, "runs": runs}, open(OUT, "w"), indent=1)
