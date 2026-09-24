#!/usr/bin/env python3
"""Prompt-logprob capture + KL/argmax comparison for the dense-FP8 gate (IP-310 L4), after upstream's
logprob_capture.py but with self-contained fixed texts (code / prose / structured log / reasoning).

  BASE=http://127.0.0.1:18000 python3 kl_probe.py capture OUT.json
  python3 kl_probe.py compare A.json B.json      # mean KL(A||B) on the top-20 support + argmax agreement
Deterministic: /v1/completions echo, max_tokens=1, temperature 0, prompt_logprobs=20.
"""
import json, math, os, sys, urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:18000")
MODEL = os.environ.get("MODEL", "glm-5.3-flash-uncensored")

CODE = '''import threading, time
from collections import OrderedDict

class TTLCache:
    """LRU cache with per-entry TTL, safe for concurrent use."""
    def __init__(self, capacity=128, ttl=60.0):
        self.capacity, self.ttl = capacity, ttl
        self._d = OrderedDict(); self._lock = threading.Lock()
    def get(self, key, default=None):
        with self._lock:
            item = self._d.get(key)
            if item is None: return default
            value, expires = item
            if time.monotonic() > expires:
                del self._d[key]; return default
            self._d.move_to_end(key); return value
    def put(self, key, value):
        with self._lock:
            self._d[key] = (value, time.monotonic() + self.ttl); self._d.move_to_end(key)
            while len(self._d) > self.capacity: self._d.popitem(last=False)

def test_expiry():
    c = TTLCache(capacity=2, ttl=0.01); c.put("a", 1); time.sleep(0.02)
    assert c.get("a") is None
'''
PROSE = ("The lighthouse at Loop Head was automated in the spring of 1973, and the keeper who had wound its clockwork "
         "for thirty-one years watched the technicians bolt a grey cabinet to the wall of the lamp room without saying "
         "much. He had expected to feel something like grief. What he felt instead was a mild, practical curiosity about "
         "whether the new lamp would rotate at exactly the same rate as the old one, because the fishermen out past the "
         "Bridges of Ross timed their turns by it, and nobody had asked them. The morning boat came at eight. ") * 3
LOG = "\n".join(f"Entry {i}: node NODE{i % 7} reported checksum CK-{i:06d} after the maintenance window; the operator logged "
                f"temperature {40 + (i * 7) % 23} C, fan duty {30 + (i * 13) % 60} percent, and no faults." for i in range(48))
REASON = ("Problem: a tree with N*K vertices must be split into N paths of exactly K vertices each. Claim: rooting the tree "
          "anywhere and processing vertices bottom-up, a vertex can pass at most one dangling path fragment to its parent. "
          "Proof sketch: if two children each returned a fragment, the vertex would have to join both, forming a path "
          "through it whose two arms could not both continue upward; so every vertex except the root either closes a "
          "path of length K or forwards exactly one open fragment. Counting fragments modulo K at each vertex decides "
          "feasibility in O(N K). Edge case: K = 1 is always feasible; K = N*K forces a Hamiltonian path. ") * 2
TEXTS = {"code": CODE, "prose": PROSE, "log48": LOG, "reason": REASON}


def capture(out):
    res = {}
    for name, text in TEXTS.items():
        body = {"model": MODEL, "prompt": text, "max_tokens": 1, "temperature": 0, "echo": True, "logprobs": 1, "prompt_logprobs": 20}
        req = urllib.request.Request(BASE + "/v1/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=600))
        pl = d["choices"][0].get("prompt_logprobs") or []
        res[name] = pl
        top1 = [max(v["logprob"] for v in pos.values()) for pos in pl[1:] if pos]
        print(f"{name:8s} positions={len(pl)} mean top-1 logprob={sum(top1) / max(1, len(top1)):.4f}")
    json.dump(res, open(out, "w"))
    print("wrote", out)


def compare(a, b):
    A = json.load(open(a)); B = json.load(open(b)); allkl = []; allagree = 0; alln = 0
    for name in A:
        kls = []; agree = 0; n = 0
        for x, y in zip(A[name][1:], B[name][1:]):
            if not x or not y: continue
            ax = {k: math.exp(v["logprob"]) for k, v in x.items()}; by = {k: math.exp(v["logprob"]) for k, v in y.items()}
            keys = set(ax) & set(by)
            if not keys: continue
            za = sum(ax[k] for k in keys); zb = sum(by[k] for k in keys)
            kls.append(sum((ax[k] / za) * math.log((ax[k] / za) / (by[k] / zb)) for k in keys))
            ta = max(x.items(), key=lambda kv: kv[1]["logprob"])[0]; tb = max(y.items(), key=lambda kv: kv[1]["logprob"])[0]
            agree += ta == tb; n += 1
        print(f"{name:8s} positions={n:5d} mean KL(A||B, top-20 support)={sum(kls) / max(1, len(kls)):.4f} nats  argmax agreement={agree / max(1, n):.3f}")
        allkl += kls; allagree += agree; alln += n
    print(f"ALL      positions={alln:5d} mean KL={sum(allkl) / max(1, len(allkl)):.4f} nats  argmax agreement={allagree / max(1, alln):.3f}")


if __name__ == "__main__":
    if sys.argv[1] == "capture": capture(sys.argv[2])
    elif sys.argv[1] == "compare": compare(sys.argv[2], sys.argv[3])
