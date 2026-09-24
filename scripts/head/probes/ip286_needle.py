#!/usr/bin/env python3
"""IP-286 Phase B — needle retrieval with EXACT-match discipline and a short-context control.

The 133k probe returned "CRIMSON-ORCHID" and dropped the "-7741" suffix. That is either a
long-context fidelity failure or a general behaviour of this model on this needle, and those
demand different conclusions -- so a SHORT-context control runs first. If the control also drops
the suffix, the effect has nothing to do with context length.

Records finish_reason to rule out output truncation, and reports EXACT vs PARTIAL separately:
a partial hit means the model located the needle but did not reproduce it faithfully, which is a
different (and more interesting) result than not finding it.
"""
import json, os, sys, time, urllib.request

URL = "http://localhost:8001/v1/chat/completions"
MODEL = "glm-5.3-flash-uncensored"
PARA = ("In distributed GPU inference, tensor parallelism splits each layer's weight matrices across "
        "devices, requiring an all-reduce after attention and MLP blocks. Pipeline parallelism instead "
        "partitions layers into stages connected by activation handoffs over the interconnect. ")
FULL = "CRIMSON-ORCHID-7741"
NEEDLE = " IMPORTANT FACT: the vault passphrase is %s. " % FULL

# (label, paras_per_side, position)
PROBES = [
    ("control ~8k",   90,   0.5),
    ("128k @10%",     1455, 0.1),
    ("128k @50%",     1455, 0.5),
    ("128k @90%",     1455, 0.9),
    ("600k @50%",     6818, 0.5),
]
if len(sys.argv) > 1:                      # allow running a subset by index
    PROBES = [PROBES[int(i)] for i in sys.argv[1:]]

out = []
for label, n, pos in PROBES:
    total = 2 * n
    before = int(round(total * pos))
    prompt = (PARA * before) + NEEDLE + (PARA * (total - before)) + (
        "\n\nQuestion: What exactly is the vault passphrase stated above? "
        "Answer with only the passphrase, exactly as written.")
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 40, "temperature": 0.0,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    t0 = time.time()
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            URL, data=body, headers={"Content-Type": "application/json"}), timeout=3600))
    except Exception as e:
        print("  %-12s ERROR %s" % (label, type(e).__name__)); continue
    dt = time.time() - t0
    m = d["choices"][0]["message"]
    ans = (m.get("content") or "").strip()
    fin = d["choices"][0]["finish_reason"]
    ptok = d["usage"]["prompt_tokens"]
    exact = FULL in ans
    partial = (not exact) and ("CRIMSON" in ans.upper())
    verdict = "EXACT" if exact else ("PARTIAL" if partial else "MISS")
    out.append({"label": label, "prompt_tokens": ptok, "position": pos, "answer": ans,
                "finish": fin, "verdict": verdict, "seconds": round(dt, 1),
                "prefill_tok_s": round(ptok / dt)})
    print("  %-12s %8d tok  %-7s  finish=%-6s %5.0fs  %6d tok/s  answer=%r"
          % (label, ptok, verdict, fin, dt, ptok / dt, ans[:40]))

json.dump(out, open("/tmp/ip286-needle.json", "w"), indent=2)
print("\nraw: /tmp/ip286-needle.json")
