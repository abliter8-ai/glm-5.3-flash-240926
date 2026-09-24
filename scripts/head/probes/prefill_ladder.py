#!/usr/bin/env python3
"""Unique-salt cold prefill ladder (IP-289 cells: ~10k / ~125k / ~378k prompt tokens).

Each rung is one request with a fresh nonce (defeats the prefix cache), temperature 0, thinking off,
max_tokens 8; prefill tok/s = prompt_tokens / wall. Runs on __HEAD_HOST__ against the ladder port unless
BASE is set. Usage: python3 prefill_ladder.py <label> [paras ...]   (default 227 2840 8600)
"""
import json, os, random, sys, time, urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8001")
MODEL = os.environ.get("MODEL", "glm-5.3-flash-uncensored")
PARA = ("In distributed GPU inference, tensor parallelism splits each layer's weight matrices across "
        "devices, requiring an all-reduce after attention and MLP blocks. Pipeline parallelism instead "
        "partitions layers into stages connected by activation handoffs over the interconnect. ")
label = sys.argv[1] if len(sys.argv) > 1 else "run"
rungs = [int(x) for x in sys.argv[2:]] or [227, 2840, 8600]
out = []
for n in rungs:
    nonce = "%06x" % random.getrandbits(24)
    prompt = "".join(f"[{nonce}-{i}] " + PARA for i in range(n)) + "\n\nSummarise the above in one word."
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 8,
                       "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        d = json.load(urllib.request.urlopen(req, timeout=7200)); dt = time.time() - t0
        pt = d["usage"]["prompt_tokens"]
        rec = {"paras": n, "prompt_tokens": pt, "seconds": round(dt, 1), "prefill_tok_s": round(pt / dt), "finish": d["choices"][0]["finish_reason"]}
    except Exception as e:
        rec = {"paras": n, "error": f"{type(e).__name__}: {e}"[:200], "seconds": round(time.time() - t0, 1)}
    out.append(rec); print(json.dumps(rec), flush=True)
path = os.path.expanduser(f"~/ip310/raw/{label}-prefill-ladder.json")
os.makedirs(os.path.dirname(path), exist_ok=True); json.dump(out, open(path, "w"), indent=1)
print("raw:", path)
