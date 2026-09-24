#!/usr/bin/env python3
"""IP-327 claim probe for GLM53_DRAFT_KV_COMPACT: repeat a ~28,672-token prefix with a short (~64-token) new tail.

Upstream receipt (README, 2026-09-21): 28,672 prefix + 64-token tail repeat 2.623 s (OFF) -> 0.302 s (ON), -90%.
Runs ON __HEAD_HOST__ against :8001. Each trial uses a fresh salted prefix: request 1 = cold prefill, requests 2 and 3 =
same prefix with a different short tail. Streams to measure time to the first delta (content or reasoning).
Usage: python3 repeat_prefix_probe.py <out.json> [trials]
"""
import json, random, statistics, sys, time, urllib.request

BASE = "http://127.0.0.1:8001"
MODEL = "glm-5.3-flash-uncensored"
TARGET_PREFIX = 28672
OUT = sys.argv[1]
TRIALS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
WORDS = ("alpha beta gamma delta epsilon zeta theta kappa lambda sigma omega river stone copper ledger harbor "
         "lantern meadow orbit quartz signal timber velvet window yellow zephyr anchor bridge candle desert").split()


def tokens(text):
    body = json.dumps({"model": MODEL, "prompt": text}).encode()
    req = urllib.request.Request(BASE + "/tokenize", data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["count"]


def make_prefix(salt):
    rng = random.Random(salt)
    words = [f"[{salt}]"]
    while True:
        words += [rng.choice(WORDS) for _ in range(2000)]
        text = " ".join(words)
        n = tokens(text)
        if n >= TARGET_PREFIX:
            break
    # trim to the target by binary search on word count
    lo, hi = 1, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if tokens(" ".join(words[:mid])) <= TARGET_PREFIX:
            lo = mid
        else:
            hi = mid - 1
    return " ".join(words[:lo])


def call(prefix, tail):
    msgs = [{"role": "user", "content": prefix + "\n\n" + tail + "\nReply with one word."}]
    body = {"model": MODEL, "messages": msgs, "max_tokens": 16, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic(); first = None; usage = {}
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            d = json.loads(line[5:])
            if d.get("usage"):
                usage = d["usage"]
            for ch in d.get("choices") or []:
                delta = ch.get("delta") or {}
                if first is None and (delta.get("content") or delta.get("reasoning") or delta.get("reasoning_content")):
                    first = time.monotonic() - t0
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    return {"ttft_s": round(first, 3) if first is not None else None, "total_s": round(time.monotonic() - t0, 3),
            "prompt_tokens": usage.get("prompt_tokens"), "cached_tokens": cached}


rows = []
for t in range(TRIALS):
    salt = f"ip327-{int(time.time())}-{t}"
    prefix = make_prefix(salt)
    tails = [" ".join(random.Random(f"{salt}-{k}").choice(WORDS) for _ in range(60)) for k in range(3)]
    cold = call(prefix, tails[0])
    rep1 = call(prefix, tails[1])
    rep2 = call(prefix, tails[2])
    rows.append({"trial": t, "cold": cold, "repeat1": rep1, "repeat2": rep2})
    print(json.dumps(rows[-1]), flush=True)
summary = {
    "prefix_tokens_target": TARGET_PREFIX,
    "cold_ttft_median_s": statistics.median(r["cold"]["ttft_s"] for r in rows),
    "repeat_ttft_median_s": statistics.median(x["ttft_s"] for r in rows for x in (r["repeat1"], r["repeat2"])),
    "repeat_cached_fraction_median": statistics.median(x["cached_tokens"] / max(1, x["prompt_tokens"] or 1)
                                                        for r in rows for x in (r["repeat1"], r["repeat2"])),
}
print("SUMMARY", json.dumps(summary))
json.dump({"rows": rows, "summary": summary}, open(OUT, "w"), indent=1)
