#!/usr/bin/env python3
"""Long-prefill stress at MAX_NUM_BATCHED_TOKENS=8192 (upstream: "never 8192 — indexer smem").

Runs ON __HEAD_HOST__ against localhost:8001. Each request is a UNIQUE (nonce-salted) prompt with a
needle so correctness is checked, not just HTTP 200. Phases:
  A  3 x ~200k tokens submitted at t=0   -> multi-sequence prefill steps (tail of one + head of next)
  B  1 x ~300k, then 1 x ~100k arriving 20 s later -> prefill/prefill mixing mid-run
  C  6 x ~60k at t=0 -> many sequences sharing 8192-token steps
Reports per request: HTTP, prompt_tokens, needle verdict, finish, elapsed, prefill tok/s.
Polls /metrics and nvidia-smi is N/A on GB10, so free -m is sampled for the host.
"""
import json, os, random, sys, threading, time, urllib.request, subprocess

URL = "http://127.0.0.1:8001/v1/chat/completions"
MODEL = "glm-5.3-flash-uncensored"
PARA = ("In distributed GPU inference, tensor parallelism splits each layer's weight matrices across "
        "devices, requiring an all-reduce after attention and MLP blocks. Pipeline parallelism instead "
        "partitions layers into stages connected by activation handoffs over the interconnect. ")
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/longprefill-stress.json"
results = []
mem = []
stop = threading.Event()


def sampler():
    while not stop.is_set():
        try:
            f = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines()[1].split()
            mem.append({"t": round(time.time() - T0, 1), "used_mb": int(f[2]), "avail_mb": int(f[6])})
        except Exception:
            pass
        time.sleep(2)


def prompt(n_paras, code):
    nonce = "%06x" % random.getrandbits(24)
    needle = f" IMPORTANT FACT: the vault passphrase is {code}. "
    before = n_paras // 2
    body = "".join(f"[{nonce}-{i}] " + PARA for i in range(before)) + needle + \
           "".join(f"[{nonce}-{i}] " + PARA for i in range(before, n_paras))
    return body + "\n\nQuestion: What exactly is the vault passphrase stated above? Answer with only the passphrase, exactly as written."


def one(label, n_paras, delay=0.0):
    time.sleep(delay)
    code = "ORCHID-%04d" % random.randint(1000, 9999)
    p = prompt(n_paras, code)
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": p}], "max_tokens": 40,
                       "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False}}).encode()
    t = time.time()
    rec = {"label": label, "submit": round(t - T0, 1), "code": code}
    try:
        r = urllib.request.urlopen(urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"}), timeout=3600)
        d = json.load(r)
        ans = (d["choices"][0]["message"].get("content") or "").strip()
        rec.update({"http": 200, "prompt_tokens": d["usage"]["prompt_tokens"], "finish": d["choices"][0]["finish_reason"],
                    "answer": ans[:40], "needle": "EXACT" if code in ans else "MISS"})
    except urllib.error.HTTPError as e:
        rec.update({"http": e.code, "error": e.read().decode()[:200]})
    except Exception as e:
        rec.update({"http": "ERR", "error": f"{type(e).__name__}: {e}"[:200]})
    rec["elapsed_s"] = round(time.time() - t, 1)
    if rec.get("prompt_tokens"):
        rec["prefill_tok_s"] = round(rec["prompt_tokens"] / rec["elapsed_s"])
    results.append(rec)
    print(f"  {label:10s} http={rec.get('http')} tok={rec.get('prompt_tokens')} needle={rec.get('needle')} finish={rec.get('finish')} "
          f"{rec['elapsed_s']}s {rec.get('prefill_tok_s','-')} tok/s {rec.get('error','')}", flush=True)


def phase(name, specs):
    print(f"== phase {name}", flush=True)
    th = [threading.Thread(target=one, args=(f"{name}-{i}", n, d)) for i, (n, d) in enumerate(specs)]
    [t.start() for t in th]
    [t.join() for t in th]
    h = urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=10).status
    print(f"   /health after phase {name}: {h}", flush=True)


T0 = time.time()
threading.Thread(target=sampler, daemon=True).start()
# ~44 tok/para: 200k ≈ 4550 paras, 300k ≈ 6800, 100k ≈ 2270, 60k ≈ 1360
phase("A", [(4550, 0), (4550, 0), (4550, 0)])
phase("B", [(6800, 0), (2270, 20)])
phase("C", [(1360, 0)] * 6)
stop.set()
peak = max((m["used_mb"] for m in mem), default=None)
json.dump({"results": results, "mem": mem, "peak_used_mb": peak}, open(OUT, "w"), indent=1)
print("peak host used MB:", peak, "| min avail MB:", min((m["avail_mb"] for m in mem), default=None))
print("raw:", OUT)
