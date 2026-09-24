#!/usr/bin/env python3
"""Long-prefill fairness probe (IP-310 item E, after upstream PR #112's measurement).

A: a warm session (~A_PARAS paragraphs, ~50k tokens) is primed once, then polls every POLL s with the
same context plus a one-line follow-up (max_tokens 16). B: B_DELAY s after A is warm, a COLD, unique
~B_PARAS-paragraph prompt (~250k tokens) is submitted (max_tokens 16). Reports A's per-poll latency
before / during / after B's prefill, B's TTFT (= its prefill wall) and prefill tok/s, the scheduler
gauges sampled every second, and the preemption-counter delta. Every number here is one draw.

  BASE=http://127.0.0.1:18000 python3 warmpoll_probe.py <label> [--a-paras 1150] [--b-paras 5700]
                                                        [--poll 25] [--b-delay 60] [--out DIR]
"""
import argparse, json, os, random, threading, time, urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:18000")
MODEL = os.environ.get("MODEL", "glm-5.3-flash-uncensored")
PARA = ("In distributed GPU inference, tensor parallelism splits each layer's weight matrices across "
        "devices, requiring an all-reduce after attention and MLP blocks. Pipeline parallelism instead "
        "partitions layers into stages connected by activation handoffs over the interconnect. ")

ap = argparse.ArgumentParser()
ap.add_argument("label")
ap.add_argument("--a-paras", type=int, default=1150)     # ~44 tok/para -> ~50k
ap.add_argument("--b-paras", type=int, default=3400)     # -> ~150k (250k = 5700; reduced 2026-09-12: L0 idle host floor 2.8 GB)
ap.add_argument("--poll", type=float, default=25.0)
ap.add_argument("--b-delay", type=float, default=60.0)
ap.add_argument("--out", default=os.path.expanduser("~/.local/share/bench/ip310-20260912"))
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)

T0 = time.time()
gauges = []
stop = threading.Event()
NONCE_A = "%06x" % random.getrandbits(24)
NONCE_B = "%06x" % random.getrandbits(24)


def metric(name_prefix):
    txt = urllib.request.urlopen(BASE + "/metrics", timeout=5).read().decode()
    for line in txt.splitlines():
        if line.startswith(name_prefix):
            return float(line.rsplit(" ", 1)[1])
    return None


def poll_metrics():
    while not stop.is_set():
        try:
            txt = urllib.request.urlopen(BASE + "/metrics", timeout=3).read().decode()
            g = {"t": round(time.time() - T0, 1)}
            for line in txt.splitlines():
                if line.startswith("vllm:num_requests_running{"):
                    g["running"] = float(line.rsplit(" ", 1)[1])
                elif line.startswith("vllm:num_requests_waiting{"):
                    g["waiting"] = float(line.rsplit(" ", 1)[1])
            gauges.append(g)
        except Exception as e:
            gauges.append({"t": round(time.time() - T0, 1), "err": type(e).__name__})
        time.sleep(1)


def paras(nonce, n):
    return "".join(f"[{nonce}-{i}] " + PARA for i in range(n))


def request(messages, max_tokens):
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.0,
                       "stream": True, "stream_options": {"include_usage": True},
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    submit = time.time()
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
    first = None; usage = None
    with urllib.request.urlopen(req, timeout=7200) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            d = json.loads(line[5:])
            if d.get("usage"):
                usage = d["usage"]
            ch = d.get("choices") or []
            if first is None and ch and (ch[0].get("delta") or {}).get("content"):
                first = time.time()
    end = time.time()
    return {"submit": round(submit - T0, 2), "ttft_s": round(first - submit, 2) if first else None,
            "total_s": round(end - submit, 2), "usage": usage}


a_ctx = paras(NONCE_A, args.a_paras) + "\n\nSummarise the two schemes above in one sentence."
a_msgs = [{"role": "user", "content": a_ctx}]
polls = []
B = {}
b_started = threading.Event(); b_done = threading.Event()


def run_b():
    b_started.set()
    b_prompt = paras(NONCE_B, args.b_paras) + "\n\nQuestion: in one sentence, what is the difference between the two schemes described above?"
    B.update(request([{"role": "user", "content": b_prompt}], 16))
    B["nonce"] = NONCE_B
    b_done.set()


poller = threading.Thread(target=poll_metrics, daemon=True); poller.start()
pre_before = metric("vllm:num_preemptions_total")
prime = request(a_msgs, 32); prime["phase"] = "prime"; polls.append(prime)
print(f"[{args.label}] A primed: {prime['usage'] and prime['usage'].get('prompt_tokens')} prompt tok, TTFT {prime['ttft_s']} s", flush=True)
t_warm = time.time()

tb = threading.Thread(target=run_b)
i = 0
while True:
    now = time.time()
    if not b_started.is_set() and now - t_warm >= args.b_delay:
        tb.start()
        time.sleep(0.5)
    i += 1
    follow = a_msgs + [{"role": "assistant", "content": "Done."}, {"role": "user", "content": f"Poll {i}: reply with the single word OK."}]
    rec = request(follow, 16)
    rec["phase"] = "before_B" if not b_started.is_set() else ("during_B" if not b_done.is_set() else "after_B")
    rec["i"] = i
    polls.append(rec)
    print(f"  poll {i:2d} [{rec['phase']:8s}] t={rec['submit']:7.1f}s  TTFT {rec['ttft_s']}s  total {rec['total_s']}s  cached={((rec['usage'] or {}).get('prompt_tokens_details') or {}).get('cached_tokens')}", flush=True)
    if b_done.is_set() and rec["phase"] == "after_B" and sum(1 for p in polls if p.get("phase") == "after_B") >= 2:
        break
    if not b_started.is_set() or not b_done.is_set():
        time.sleep(max(0.0, args.poll - rec["total_s"]))
    else:
        time.sleep(2)
tb.join(); stop.set()
pre_after = metric("vllm:num_preemptions_total")

during = [p["total_s"] for p in polls if p.get("phase") == "during_B"]
before = [p["total_s"] for p in polls if p.get("phase") == "before_B"]
b_tok = (B.get("usage") or {}).get("prompt_tokens")
summary = {"label": args.label, "a_prompt_tokens": (prime["usage"] or {}).get("prompt_tokens"), "b_prompt_tokens": b_tok,
           "b_ttft_s": B.get("ttft_s"), "b_prefill_tok_s": round(b_tok / B["ttft_s"], 1) if b_tok and B.get("ttft_s") else None,
           "a_poll_total_s_before_B": before, "a_poll_total_s_during_B": during,
           "a_poll_max_during_B": max(during) if during else None, "a_polls_during_B": len(during),
           "preemptions_delta": (pre_after - pre_before) if (pre_after is not None and pre_before is not None) else None,
           "peak_running": max((g.get("running", 0) for g in gauges), default=0),
           "peak_waiting": max((g.get("waiting", 0) for g in gauges), default=0)}
out = {"summary": summary, "polls": polls, "B": B, "gauges": gauges}
path = os.path.join(args.out, f"{args.label}-warmpoll.json")
json.dump(out, open(path, "w"), indent=1)
print(json.dumps(summary, indent=1)); print("raw:", path)
