#!/usr/bin/env python3
"""Staggered-arrival admission probe for GLM53_MIXED_PREFILL_CHUNK (IP-286 finding 3).

A: a decode-heavy caller starts first. B: a long-prompt caller arrives DELAY seconds after A's
first token. Reports B's TTFT and A's decode rate before / during / after B's prefill, plus the
scheduler's running/deferred gauges sampled every second. Every number here is one draw.

  python3 stagger_probe.py <label> [--delay 5] [--b-paras 400] [--a-max 1200]
"""
import argparse, json, os, sys, threading, time, urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:18000")
MODEL = "glm-5.3-flash-uncensored"
PARA = ("In distributed GPU inference, tensor parallelism splits each layer's weight matrices across "
        "devices, requiring an all-reduce after attention and MLP blocks. Pipeline parallelism instead "
        "partitions layers into stages connected by activation handoffs over the interconnect. ")

ap = argparse.ArgumentParser()
ap.add_argument("label")
ap.add_argument("--delay", type=float, default=5.0)
ap.add_argument("--b-paras", type=int, default=400)      # ~44 tok/para -> ~17.6k prompt tokens
ap.add_argument("--a-max", type=int, default=1200)
ap.add_argument("--out", default=os.path.expanduser(
    "~/.local/share/bench/glm53-exl3-ip286-quality-20260901/prefill-policy"))
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)

T0 = time.time()
gauges = []
stop = threading.Event()


def poll_metrics():
    while not stop.is_set():
        try:
            txt = urllib.request.urlopen(BASE + "/metrics", timeout=3).read().decode()
            g = {"t": round(time.time() - T0, 1)}
            for line in txt.splitlines():
                if line.startswith("vllm:num_requests_running{"):
                    g["running"] = float(line.rsplit(" ", 1)[1])
                elif line.startswith("vllm:num_requests_waiting_by_reason{") and 'reason="deferred"' in line:
                    g["deferred"] = float(line.rsplit(" ", 1)[1])
                elif line.startswith("vllm:num_requests_waiting_by_reason{") and 'reason="capacity"' in line:
                    g["capacity"] = float(line.rsplit(" ", 1)[1])
            gauges.append(g)
        except Exception as e:
            gauges.append({"t": round(time.time() - T0, 1), "err": type(e).__name__})
        time.sleep(1)


def stream(name, prompt, max_tokens, rec):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": 1.0, "top_p": 0.95, "stream": True,
                       "stream_options": {"include_usage": True},
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    rec["submit"] = round(time.time() - T0, 2)
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    stamps = []; chars = []
    rec["_stamps"] = stamps; rec["_chars"] = chars        # shared live, so the launcher can watch A
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            d = json.loads(line[5:])
            if d.get("usage"):
                rec["usage"] = d["usage"]
            ch = d.get("choices") or []
            if ch and (ch[0].get("delta") or {}).get("content"):
                stamps.append(time.time() - T0); chars.append(len(ch[0]["delta"]["content"]))
    rec["first_token"] = round(stamps[0], 2) if stamps else None
    rec["last_token"] = round(stamps[-1], 2) if stamps else None
    rec["ttft_s"] = round(stamps[0] - rec["submit"], 2) if stamps else None
    rec["n_chunks"] = len(stamps)


def rate(rec, lo, hi):
    st, ch = rec["_stamps"], rec["_chars"]
    tot = sum(ch) or 1
    toks = rec.get("usage", {}).get("completion_tokens", 0)
    share = sum(c for s, c in zip(st, ch) if lo <= s < hi) / tot
    return round(toks * share / (hi - lo), 1) if hi > lo else None


import random
NONCE = "%06x" % random.getrandbits(24)     # unique per run: defeats the block-aligned prefix cache
def paras(n):
    return "".join(f"[{NONCE}-{i}] " + PARA for i in range(n))
A = {"name": "A", "prompt_paras": 30, "nonce": NONCE}
B = {"name": "B", "prompt_paras": args.b_paras, "nonce": NONCE}
a_prompt = paras(30) + ("\n\nWrite a long, detailed technical essay (at least 1500 words) on how the "
                        "tradeoffs above change for a mixture-of-experts model served across two nodes.")
b_prompt = paras(args.b_paras) + "\n\nQuestion: in one sentence, what is the difference between the two schemes described above?"

poller = threading.Thread(target=poll_metrics, daemon=True); poller.start()
ta = threading.Thread(target=stream, args=("A", a_prompt, args.a_max, A)); ta.start()
# wait for A's first token, then DELAY seconds of decode
while not A.get("_stamps") and ta.is_alive():
    time.sleep(0.05)
a_first = A["_stamps"][0] if A.get("_stamps") else time.time() - T0
time.sleep(args.delay)
tb = threading.Thread(target=stream, args=("B", b_prompt, 32, B)); tb.start()
ta.join(); tb.join(); stop.set()

# A's decode rate before / during / after B's prefill window
b_sub = B["submit"]; b_ft = B["first_token"] or b_sub
A["decode_tps_before_B"] = rate(A, a_first, b_sub)
A["decode_tps_during_B_prefill"] = rate(A, b_sub, b_ft)
A["decode_tps_after_B_first_token"] = rate(A, b_ft, A["last_token"]) if A["last_token"] and A["last_token"] > b_ft else None
A["decode_tps_overall"] = rate(A, a_first, A["last_token"])
for r in (A, B):
    r.pop("_stamps", None); r.pop("_chars", None)

peak_running = max((g.get("running", 0) for g in gauges), default=0)
peak_deferred = max((g.get("deferred", 0) for g in gauges), default=0)
out = {"label": args.label, "delay_s": args.delay, "A": A, "B": B,
       "peak_running": peak_running, "peak_deferred": peak_deferred, "gauges": gauges}
path = os.path.join(args.out, f"{args.label}.json")
json.dump(out, open(path, "w"), indent=1)
print(f"[{args.label}] B prompt {B.get('usage', {}).get('prompt_tokens')} tok  TTFT_B = {B['ttft_s']} s   "
      f"(A finished at t={A['last_token']}, B first token at t={B['first_token']})")
print(f"  A decode tok/s: before B {A['decode_tps_before_B']} | during B prefill {A['decode_tps_during_B_prefill']} "
      f"| after {A['decode_tps_after_B_first_token']} | overall {A['decode_tps_overall']}  (A {A.get('usage', {}).get('completion_tokens')} tok)")
print(f"  peak running {peak_running}  peak deferred {peak_deferred}   raw: {path}")
