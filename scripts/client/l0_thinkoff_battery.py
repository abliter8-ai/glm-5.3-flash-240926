#!/usr/bin/env python3
"""IP-310 L0 thinking-off battery — the template gate (upstream PR #150 / our KI-080).

Direct to the engine (BASE), thinking OFF unless stated, serving sampling T 1.0 / top_p 0.95:
  1. story brief x N (creative_temp_sweep BRIEF, max_tokens 1800): finish reason, words, text stats,
     and a planning-leak heuristic (outline / "let me" / "start over" markers in the content).
  2. JSON extraction (accept_probe case): valid JSON array of 4 items + DFlash2 acceptance delta.
  3. code: LRU cache module thinking OFF (max_tokens 3000): fence count, SyntaxError check of the
     largest python block, finish reason (upstream's CODE-12 symptom was 5-6 fences + SyntaxError).
  4. six-class acceptance probe is run separately (accept_probe.py); LCB trio via livecodebench_suite.

  BASE=http://127.0.0.1:18000 python3 l0_thinkoff_battery.py <label> [--stories 4] [--out DIR]
"""
import argparse, ast, json, os, re, time, urllib.request
from collections import Counter

BASE = os.environ.get("BASE", "http://127.0.0.1:18000")
MODEL = os.environ.get("MODEL", "glm-5.3-flash-uncensored")
K = 7
BRIEF = ("Write a short story of about 900 words. Setting: a lighthouse on the west coast of Ireland "
         "in 1973, the night before it is automated and its keeper leaves for good. Third person, past "
         "tense, one point of view. The keeper is not sentimental about the machine that replaces him; "
         "the story should find its feeling somewhere unexpected. No headings, no epigraph, no lists. "
         "End on an image, not a moral.")
JSON_PROMPT = ("Extract every product mentioned into a JSON array of {name, price, currency}. Text: 'The ACME kettle "
               "sells for 39.99 EUR, the Bosch toaster for 45 EUR and the Philips airfryer for 129.50 USD; the Krups "
               "grinder is 60 EUR.' Output only JSON.")
CODE_PROMPT = "Write a Python module implementing an LRU cache with TTL expiry, thread-safe, with a small pytest suite. Return only code."
LEAK = re.compile(r"(let me (start over|try again|rewrite)|start over|here'?s (the|a) (plan|outline)|^\s*(outline|plan)\s*:|\*\*(outline|plan)\*\*|"
                  r"^\s*[-*]\s+\w.*\n\s*[-*]\s+\w.*\n\s*[-*]\s+)", re.I | re.M)

ap = argparse.ArgumentParser()
ap.add_argument("label"); ap.add_argument("--stories", type=int, default=4)
ap.add_argument("--out", default=os.path.expanduser("~/.local/share/bench/ip310-20260912"))
args = ap.parse_args(); os.makedirs(args.out, exist_ok=True)
sdir = os.path.join(args.out, f"{args.label}-stories"); os.makedirs(sdir, exist_ok=True)


def metrics():
    txt = urllib.request.urlopen(BASE + "/metrics", timeout=30).read().decode()
    out = {"drafts": 0.0, "draft_tokens": 0.0, "accepted": 0.0}
    for line in txt.splitlines():
        if line.startswith("vllm:spec_decode_num_drafts_total"): out["drafts"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:spec_decode_num_draft_tokens_total"): out["draft_tokens"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:spec_decode_num_accepted_tokens_total"): out["accepted"] = float(line.rsplit(" ", 1)[1])
    return out


def chat(prompt, max_tokens, thinking=False, effort=None, T=1.0, top_p=0.95):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": T, "top_p": top_p,
            "max_tokens": max_tokens, "stream": False,
            "chat_template_kwargs": {"enable_thinking": thinking, **({"reasoning_effort": effort} if (thinking and effort) else {})}}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    m0 = metrics(); t0 = time.time()
    resp = json.loads(urllib.request.urlopen(req, timeout=1800).read().decode())
    dt = time.time() - t0; m1 = metrics()
    ch = resp["choices"][0]; msg = ch["message"]
    d = m1["drafts"] - m0["drafts"]; dtok = m1["draft_tokens"] - m0["draft_tokens"]; acc = m1["accepted"] - m0["accepted"]
    comp = resp.get("usage", {}).get("completion_tokens", 0)
    return {"content": msg.get("content") or "", "reasoning_chars": len(msg.get("reasoning_content") or msg.get("reasoning") or ""),
            "finish": ch.get("finish_reason"), "completion_tokens": comp, "wall_s": round(dt, 1),
            "tok_s": round(comp / dt, 1) if dt else None, "accept_rate": round(acc / dtok, 3) if dtok else None,
            "tokens_per_step": round(1 + acc / d, 2) if d else None}


def text_stats(t):
    words = re.findall(r"[A-Za-z']+", t.lower()); n = len(words)
    grams4 = Counter(tuple(words[i:i + 4]) for i in range(max(0, n - 3)))
    rep4 = sum(c for c in grams4.values() if c > 1)
    g3 = [tuple(words[i:i + 3]) for i in range(max(0, n - 2))]
    return {"words": n, "distinct3": round(len(set(g3)) / len(g3), 3) if g3 else None,
            "repeated_4gram_share": round(rep4 / max(1, n - 3), 3), "leak_markers": len(LEAK.findall(t))}


rows = {"label": args.label, "stories": [], "json": None, "code": None}
for i in range(args.stories):
    r = chat(BRIEF, 1800); st = text_stats(r["content"])
    usable = (r["finish"] == "stop" and 600 <= st["words"] <= 1400 and st["repeated_4gram_share"] < 0.05 and st["leak_markers"] == 0)
    open(os.path.join(sdir, f"story{i + 1}.txt"), "w").write(r["content"])
    row = {"i": i + 1, **{k: v for k, v in r.items() if k != "content"}, **st, "usable": usable}
    rows["stories"].append(row); print(json.dumps(row), flush=True)

r = chat(JSON_PROMPT, 400)
txt = r["content"].strip(); txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
valid = None
try:
    parsed = json.loads(txt); valid = isinstance(parsed, list) and len(parsed) == 4 and all({"name", "price", "currency"} <= set(x) for x in parsed)
except Exception:
    valid = False
rows["json"] = {**{k: v for k, v in r.items() if k != "content"}, "raw_json_no_fence": not r["content"].strip().startswith("```"), "valid_4_items": valid}
print(json.dumps(rows["json"]), flush=True)

r = chat(CODE_PROMPT, 3000)
blocks = re.findall(r"```(?:python)?\n(.*?)```", r["content"], re.S)
biggest = max(blocks, key=len) if blocks else r["content"]
try:
    ast.parse(biggest); syntax_ok = True
except SyntaxError:
    syntax_ok = False
rows["code"] = {**{k: v for k, v in r.items() if k != "content"}, "fences": len(blocks), "syntax_ok": syntax_ok, "chars": len(r["content"])}
open(os.path.join(sdir, "code.txt"), "w").write(r["content"])
print(json.dumps(rows["code"]), flush=True)

usable = sum(1 for s in rows["stories"] if s["usable"])
rows["summary"] = {"stories_usable": f"{usable}/{len(rows['stories'])}", "json_valid": rows["json"]["valid_4_items"],
                   "json_tokens_per_step": rows["json"]["tokens_per_step"], "code_syntax_ok": rows["code"]["syntax_ok"], "code_fences": rows["code"]["fences"]}
path = os.path.join(args.out, f"{args.label}-thinkoff.json"); json.dump(rows, open(path, "w"), indent=1)
print("SUMMARY", json.dumps(rows["summary"])); print("raw:", path)
